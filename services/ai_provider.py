"""How AI work gets done. The rest of the app doesn't care which mode or provider is active.

- chatgpt_manual: the app builds a prompt, you paste it into ChatGPT (your Plus subscription,
  no extra cost) and paste the reply back. Default.
- api: the app calls an AI provider's API directly, billed per use by that provider:
  OpenAI, Anthropic (Claude), DeepSeek, or any other OpenAI-compatible service.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from core.config import load_config
from db.repository import Repository
from services import ai_client, claude_client
from services.secrets import require_api_key

AI_MODES = {
    "chatgpt_manual": "ChatGPT subscription — copy & paste (no extra cost)",
    "api": "AI provider API — automatic (billed per use by the provider)",
}
SYSTEM_MESSAGE = ("You assist a university mathematics tutor. Follow the task's rules and output format "
                  "exactly, and reply with the JSON only.")


@dataclass(frozen=True)
class ProviderInfo:
    label: str
    key_name: str              # variable in .env
    kind: str                  # "openai" (OpenAI-compatible SDK) or "anthropic"
    default_model: str
    base_url: str | None = None
    suggested_models: tuple[str, ...] = ()
    reads_files: tuple[str, ...] = ()  # MIME types / prefixes it can receive as files


PROVIDERS: dict[str, ProviderInfo] = {
    "openai": ProviderInfo("OpenAI", "OPENAI_API_KEY", "openai", "gpt-4.1-mini",
                           reads_files=("application/pdf", "image/")),
    "anthropic": ProviderInfo("Anthropic (Claude)", "ANTHROPIC_API_KEY", "anthropic", claude_client.DEFAULT_MODEL,
                              suggested_models=tuple(claude_client.SUGGESTED_MODELS),
                              reads_files=("application/pdf", "image/")),
    "deepseek": ProviderInfo("DeepSeek", "DEEPSEEK_API_KEY", "openai", "deepseek-chat",
                             base_url="https://api.deepseek.com"),
    "compatible": ProviderInfo("Other OpenAI-compatible (custom URL)", "COMPATIBLE_API_KEY", "openai", ""),
}


# ------------------------------------------------------------------ settings
def current_mode(repo: Repository) -> str:
    mode = repo.get_setting("ai_mode") or load_config()["ai"].get("default_mode", "chatgpt_manual")
    if mode == "openai_api":  # older setting value
        return "api"
    return mode if mode in AI_MODES else "chatgpt_manual"


def current_provider(repo: Repository) -> str:
    provider = repo.get_setting("api_provider") or "openai"
    return provider if provider in PROVIDERS else "openai"


def provider_model(repo: Repository, provider: str) -> str:
    saved = repo.get_setting(f"ai_model_{provider}")
    if saved:
        return saved
    if provider == "openai":  # older single-provider setting, then config
        return repo.get_setting("ai_model") or load_config()["ai"].get("default_model") or PROVIDERS["openai"].default_model
    return PROVIDERS[provider].default_model


def provider_base_url(repo: Repository, provider: str) -> str | None:
    if provider == "compatible":
        return repo.get_setting("compatible_base_url") or None
    return PROVIDERS[provider].base_url


def list_provider_models(repo: Repository, provider: str, api_key: str) -> list[str]:
    info = PROVIDERS[provider]
    if info.kind == "anthropic":
        return claude_client.list_models(api_key=api_key)
    return ai_client.list_models(api_key=api_key, base_url=provider_base_url(repo, provider),
                                 chat_only=provider == "openai")


# ----------------------------------------------------------------- providers
class AIProvider(ABC):
    mode: str
    is_manual: bool

    @abstractmethod
    def complete(self, prompt: str, files: list | None = None) -> str | None:
        """Return the model's reply text, or None if the user will paste the reply in manually.
        `files`: FilePart objects to send with the prompt (only if `accepts` allows them)."""

    @property
    def accepts(self) -> set[str]:
        """MIME types / prefixes this provider can receive as files (empty = text only)."""
        return set()

    @property
    @abstractmethod
    def model_label(self) -> str:
        """Recorded with each generated question so you know where it came from."""


class ManualChatGPTProvider(AIProvider):
    mode, is_manual = "chatgpt_manual", True

    def complete(self, prompt: str, files: list | None = None) -> None:
        return None

    @property
    def model_label(self) -> str:
        return "chatgpt-manual"


class APIProvider(AIProvider):
    mode, is_manual = "api", False

    def __init__(self, provider: str, model: str, base_url: str | None = None):
        self.provider, self.model, self.base_url = provider, model, base_url
        self.info = PROVIDERS[provider]

    @property
    def accepts(self) -> set[str]:
        return set(self.info.reads_files)

    def complete(self, prompt: str, files: list | None = None) -> str:
        if files and not self.accepts:
            raise ValueError(f"{self.info.label} can't receive files in this app; use text documents instead.")
        if not self.model:
            raise ValueError(f"No model chosen for {self.info.label}. Set one in Settings.")
        api_key = require_api_key(self.info.key_name, self.info.label)
        if self.info.kind == "anthropic":
            return claude_client.complete(api_key=api_key, model=self.model, system=SYSTEM_MESSAGE, prompt=prompt,
                                          files=files)
        if self.provider == "compatible" and not self.base_url:
            raise ValueError("Set the base URL for the OpenAI-compatible provider in Settings.")
        return ai_client.complete(api_key=api_key, base_url=self.base_url, model=self.model,
                                  system=SYSTEM_MESSAGE, prompt=prompt, files=files)

    @property
    def model_label(self) -> str:
        return f"{self.provider}:{self.model}"


def get_provider(repo: Repository) -> AIProvider:
    if current_mode(repo) == "api":
        provider = current_provider(repo)
        return APIProvider(provider, provider_model(repo, provider), provider_base_url(repo, provider))
    return ManualChatGPTProvider()


def mode_label(repo: Repository) -> str:
    """Human-readable description of the active mode, e.g. for page captions."""
    if current_mode(repo) == "api":
        provider = current_provider(repo)
        return f"API — {PROVIDERS[provider].label} · {provider_model(repo, provider) or '(no model set)'}"
    return AI_MODES["chatgpt_manual"]
