"""AI provider routing, with fake SDK clients (no network, no cost)."""
from types import SimpleNamespace

import pytest

from db.repository import Repository
from services import ai_client, claude_client, secrets
from services.ai_provider import (
    APIProvider,
    ManualChatGPTProvider,
    current_mode,
    get_provider,
    mode_label,
    provider_base_url,
    provider_model,
)


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "t.db")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point key storage at a temporary .env and clear real keys from the environment."""
    path = tmp_path / ".env"
    monkeypatch.setattr(secrets, "env_path", lambda: path)
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "COMPATIBLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return path


# ------------------------------------------------------------------- Claude
class FakeAnthropic:
    def __init__(self, response):
        self.calls = []
        record = lambda **kw: (self.calls.append(kw), response)[1]  # noqa: E731
        self.messages = SimpleNamespace(create=lambda **kw: record(kind="plain", **kw))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: record(kind="beta", **kw)))


def claude_response(stop_reason="end_turn", text='{"ok": true}'):
    return SimpleNamespace(stop_reason=stop_reason, stop_details=SimpleNamespace(category="cyber"),
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=text)])


def test_claude_opus_uses_fallback_and_effort(monkeypatch):
    fake = FakeAnthropic(claude_response())
    monkeypatch.setattr(claude_client, "_client", lambda key: fake)
    out = claude_client.complete(api_key="k", model="claude-opus-5-5", system="S", prompt="P")
    assert out == '{"ok": true}'  # thinking blocks ignored
    call = fake.calls[0]
    assert call["kind"] == "beta" and call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"] == {"effort": "high"} and call["system"] == "S"
    assert "thinking" not in call  # adaptive by default on current models


def test_claude_haiku_has_no_fallback(monkeypatch):
    fake = FakeAnthropic(claude_response())
    monkeypatch.setattr(claude_client, "_client", lambda key: fake)
    claude_client.complete(api_key="k", model="claude-haiku-5-5", system="S", prompt="P")
    assert fake.calls[0]["kind"] == "plain" and "fallbacks" not in fake.calls[0]


def test_claude_refusal_and_truncation_raise(monkeypatch):
    monkeypatch.setattr(claude_client, "_client", lambda key: FakeAnthropic(claude_response("refusal", "")))
    with pytest.raises(claude_client.ClaudeRefusal, match="cyber"):
        claude_client.complete(api_key="k", model="claude-opus-5-5", system="S", prompt="P")
    monkeypatch.setattr(claude_client, "_client", lambda key: FakeAnthropic(claude_response("max_tokens")))
    with pytest.raises(RuntimeError, match="cut off"):
        claude_client.complete(api_key="k", model="claude-opus-5-5", system="S", prompt="P")


# ------------------------------------------------------- OpenAI-compatible
class FakeOpenAI:
    def __init__(self):
        self.calls = []

        def create(**kw):
            self.calls.append(kw)
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                                                            message=SimpleNamespace(content="{}"))])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_deepseek_routes_through_openai_client_with_base_url(repo, env, monkeypatch):
    seen = {}
    fake = FakeOpenAI()

    def fake_get_client(api_key, base_url=None):
        seen.update(api_key=api_key, base_url=base_url)
        return fake

    monkeypatch.setattr(ai_client, "get_client", fake_get_client)
    secrets.save_api_key("ds-key-123456", key_name="DEEPSEEK_API_KEY")
    repo.set_setting("ai_mode", "api")
    repo.set_setting("api_provider", "deepseek")
    provider = get_provider(repo)
    assert isinstance(provider, APIProvider) and provider.model_label == "deepseek:deepseek-chat"
    assert provider.complete("hello") == "{}"
    assert seen == {"api_key": "ds-key-123456", "base_url": "https://api.deepseek.com"}
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    assert "DeepSeek" in mode_label(repo)


def test_anthropic_provider_routes_to_claude(repo, env, monkeypatch):
    fake = FakeAnthropic(claude_response())
    monkeypatch.setattr(claude_client, "_client", lambda key: fake)
    secrets.save_api_key("sk-ant-test-12345", key_name="ANTHROPIC_API_KEY")
    repo.set_setting("ai_mode", "api")
    repo.set_setting("api_provider", "anthropic")
    provider = get_provider(repo)
    assert provider.model_label == "anthropic:claude-opus-5-5"
    provider.complete("hi")
    assert fake.calls[0]["model"] == "claude-opus-5-5"


def test_missing_key_and_missing_url(repo, env):
    repo.set_setting("ai_mode", "api")
    repo.set_setting("api_provider", "anthropic")
    with pytest.raises(secrets.MissingAPIKey, match="Anthropic"):
        get_provider(repo).complete("x")
    repo.set_setting("api_provider", "compatible")
    repo.set_setting("ai_model_compatible", "qwen-max")
    secrets.save_api_key("key-abcdef123", key_name="COMPATIBLE_API_KEY")
    with pytest.raises(ValueError, match="base URL"):
        get_provider(repo).complete("x")
    repo.set_setting("compatible_base_url", "https://example.test/v1")
    assert provider_base_url(repo, "compatible") == "https://example.test/v1"


# ------------------------------------------------------------- settings
def test_legacy_settings_still_work(repo):
    repo.set_setting("ai_mode", "openai_api")  # value written by earlier versions
    repo.set_setting("ai_model", "gpt-old")
    assert current_mode(repo) == "api"
    assert provider_model(repo, "openai") == "gpt-old"
    repo.set_setting("ai_mode", "chatgpt_manual")
    assert isinstance(get_provider(repo), ManualChatGPTProvider)


def test_keys_are_stored_per_provider(env):
    secrets.save_api_key("openai-key-1234", key_name="OPENAI_API_KEY")
    secrets.save_api_key("claude-key-5678", key_name="ANTHROPIC_API_KEY")
    assert secrets.get_api_key(key_name="OPENAI_API_KEY") == "openai-key-1234"
    assert secrets.get_api_key(key_name="ANTHROPIC_API_KEY") == "claude-key-5678"
    secrets.clear_api_key(key_name="OPENAI_API_KEY")
    assert secrets.get_api_key(key_name="OPENAI_API_KEY") is None
    assert secrets.get_api_key(key_name="ANTHROPIC_API_KEY") == "claude-key-5678"
