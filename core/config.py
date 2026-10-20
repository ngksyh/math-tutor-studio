"""Load config.yaml and resolve project paths."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_path(relative: str) -> Path:
    """Resolve a config path relative to the project root."""
    path = Path(relative)
    return path if path.is_absolute() else PROJECT_ROOT / path


DEMO_DATABASE = "data/demo.db"


def is_demo() -> bool:
    """Demo mode (MTS_DEMO=1): a separate database filled with sample content, for reviewers."""
    return os.environ.get("MTS_DEMO", "").strip().lower() in ("1", "true", "yes")


def database_path() -> Path:
    """MTS_DB_PATH overrides everything (used by the UI tests); demo mode uses its own database."""
    if os.environ.get("MTS_DB_PATH"):
        return resolve_path(os.environ["MTS_DB_PATH"])
    if is_demo():
        return resolve_path(DEMO_DATABASE)
    return resolve_path(load_config()["app"]["database_path"])


def env_path() -> Path:
    return resolve_path(load_config()["app"].get("env_path", ".env"))
