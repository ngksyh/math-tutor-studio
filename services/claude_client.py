"""Claude through Anthropic's official SDK (`anthropic`).

- Default model: Claude Opus 5.5 (`claude-opus-5-5`); thinking is adaptive by default on current
  models, and effort is set to "high" because maths generation and checking are reasoning-heavy
  (Opus 5.5's own default is "medium").
- On models that support it, requests opt into Anthropic's server-side fallback
  (`fallbacks: "default"`): if a safety classifier declines a request, the API re-runs it on the
  model Anthropic recommends for that case instead of returning a refusal.
"""
from __future__ import annotations

import anthropic

from services.secrets import MissingAPIKey

DEFAULT_MODEL = "claude-opus-5-5"
SUGGESTED_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
EFFORT = "high"
MAX_TOKENS = 16000  # non-streaming; ample for a batch of questions or a set of drafts
# Models that accept the server-side fallback; Haiku 5.5 must not be sent it.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeRefusal(RuntimeError):
    pass


def _client(api_key: str) -> anthropic.Anthropic:
    if not api_key:
        raise MissingAPIKey("No Anthropic API key saved. Add one in Settings.")
    return anthropic.Anthropic(api_key=api_key)


def _supports_effort(model: str) -> bool:
    # effort errors on older models such as Haiku 4.5 / Sonnet 4.5
    return not any(old in model for old in ("haiku-4", "sonnet-4-5", "claude-3"))


def _file_block(part) -> dict:
    """PDFs as `document` blocks, images as `image` blocks (base64)."""
    source = {"type": "base64", "media_type": part.mime, "data": part.b64}
    if part.mime == "application/pdf":
        return {"type": "document", "source": source, "title": part.name}
    return {"type": "image", "source": source}


def complete(*, api_key: str, model: str, system: str, prompt: str, files: list | None = None) -> str:
    """`files`: FilePart objects (PDF / images) to send with the prompt."""
    client = _client(api_key)
    # Files go before the text block, as Anthropic's docs recommend.
    content = [_file_block(p) for p in files or []] + [{"type": "text", "text": prompt}] if files else prompt
    request = dict(model=model, max_tokens=MAX_TOKENS, system=system,
                   messages=[{"role": "user", "content": content}])
    if _supports_effort(model):
        request["output_config"] = {"effort": EFFORT}
    if model in FALLBACK_MODELS:
        response = client.beta.messages.create(**request, betas=[FALLBACK_BETA], fallbacks="default")
    else:
        response = client.messages.create(**request)

    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise ClaudeRefusal("Claude declined this request" + (f" (category: {category})." if category else ".")
                            + " Try rephrasing, or use the ChatGPT copy-and-paste mode for this step.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's reply was cut off (output limit reached). Try fewer items per request.")
    return "".join(block.text for block in response.content if block.type == "text")


def list_models(*, api_key: str) -> list[str]:
    """Verify the key (no tokens used) and return the available Claude model IDs."""
    return sorted(m.id for m in _client(api_key).models.list())
