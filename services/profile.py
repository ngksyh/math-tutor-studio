"""Your public tutor profile — the only source of credentials and services used in drafts."""
from __future__ import annotations

from core.config import load_config
from db.repository import Repository

PROFILE_FIELDS = {
    "credentials": "Credentials (only these may be mentioned)",
    "services": "Services",
    "teaching_style": "Teaching style",
    "contact": "How students should contact you",
}


def current_profile(repo: Repository) -> dict[str, str]:
    profile = {k: str(v).strip() for k, v in load_config().get("profile", {}).items() if k in PROFILE_FIELDS}
    profile.update({k: v for k, v in (repo.get_json_setting("tutor_profile") or {}).items() if k in PROFILE_FIELDS})
    return {k: profile.get(k, "") for k in PROFILE_FIELDS}


def profile_block(profile: dict[str, str]) -> str:
    return "\n".join(f"**{PROFILE_FIELDS[k]}:**\n{v or '(not set)'}" for k, v in profile.items())
