"""OpenAI-compatible chat APIs: OpenAI itself, DeepSeek, and any other OpenAI-compatible service
(Qwen, Kimi, OpenRouter, …) reached through a base URL. Claude has its own client
(services/claude_client.py), using Anthropic's SDK."""
from __future__ import annotations

import openai
from openai import OpenAI

from services.secrets import MissingAPIKey  # noqa: F401 — re-exported for callers


def get_client(api_key: str, base_url: str | None = None) -> OpenAI:
    if not api_key:
        raise MissingAPIKey("No API key saved for this provider. Add one in Settings.")
    return OpenAI(api_key=api_key, base_url=base_url or None, timeout=300)


def _file_part(part) -> dict:
    data_url = f"data:{part.mime};base64,{part.b64}"
    if part.mime == "application/pdf":
        return {"type": "file", "file": {"filename": f"{part.name}.pdf", "file_data": data_url}}
    return {"type": "image_url", "image_url": {"url": data_url}}


def complete(*, api_key: str, base_url: str | None, model: str, system: str, prompt: str,
             files: list | None = None) -> str:
    """One chat completion. Asks for JSON output; services that don't support that mode get a
    plain request instead (the reply is validated by the app either way).
    `files`: FilePart objects (PDF / images) — only for providers that read them (OpenAI)."""
    client = get_client(api_key, base_url)
    user = [{"type": "text", "text": prompt}] + [_file_part(p) for p in files] if files else prompt
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        response = client.chat.completions.create(model=model, messages=messages,
                                                  response_format={"type": "json_object"})
    except openai.BadRequestError:
        response = client.chat.completions.create(model=model, messages=messages)
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("The reply was cut off (output limit reached). Try fewer items per request.")
    return choice.message.content or ""


def list_models(*, api_key: str, base_url: str | None = None, chat_only: bool = False) -> list[str]:
    """Verify the key (free call — no tokens used) and return the available model IDs."""
    ids = [m.id for m in get_client(api_key, base_url).models.list()]
    if chat_only:  # OpenAI's own list mixes in audio, image and embedding models
        excluded = ("audio", "realtime", "transcribe", "tts", "image", "embedding", "moderation",
                    "search", "dall-e", "whisper", "instruct")
        ids = [i for i in ids if i.startswith(("gpt-", "o1", "o3", "o4", "o5"))
               and not any(word in i for word in excluded)]
    return sorted(ids)
