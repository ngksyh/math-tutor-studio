"""Local storage for AI provider API keys.

Keys live in a .env file in the project folder (git-ignored), one variable per provider
(OPENAI_API_KEY, ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, COMPATIBLE_API_KEY). They are plain text on
your own disk, so don't sync this folder to public cloud storage or share it.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, set_key, unset_key

from core.config import env_path

KEY_NAME = "OPENAI_API_KEY"  # default (the original single-provider setup)


class MissingAPIKey(RuntimeError):
    pass


def get_api_key(path: Path | None = None, key_name: str = KEY_NAME) -> str | None:
    path = path or env_path()
    value = dotenv_values(path).get(key_name) if path.exists() else None
    return (value or os.environ.get(key_name) or "").strip() or None


def require_api_key(key_name: str, provider_label: str) -> str:
    key = get_api_key(key_name=key_name)
    if not key:
        raise MissingAPIKey(f"No {provider_label} API key saved. Add one in Settings.")
    return key


def save_api_key(key: str, path: Path | None = None, key_name: str = KEY_NAME) -> None:
    key = key.strip()
    if not key:
        raise ValueError("API key is empty.")
    if any(ch.isspace() for ch in key):
        raise ValueError("API key must not contain spaces or line breaks.")
    path = path or env_path()
    path.touch(exist_ok=True)
    set_key(str(path), key_name, key, quote_mode="never")
    os.environ[key_name] = key


def clear_api_key(path: Path | None = None, key_name: str = KEY_NAME) -> None:
    path = path or env_path()
    if path.exists():
        unset_key(str(path), key_name, quote_mode="never")
    os.environ.pop(key_name, None)


def mask_key(key: str | None) -> str:
    if not key:
        return "not set"
    if len(key) <= 10:
        return "•" * len(key)
    return f"{key[:3]}…{key[-4:]}"
