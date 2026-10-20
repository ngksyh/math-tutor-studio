"""Turn YOUR approved solution into bilingual platform drafts, and check what comes back.

Drafts are never published automatically. Problems found by the checks (length limits, promises
of results, invented prices, LaTeX in captions, wrong language) are stored as flags on the post
so you see them while editing — they don't silently block anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from core.config import load_config
from db.models import CTA_STYLES, PLATFORM_LANGUAGE, PLATFORMS, new_id
from db.repository import Repository
from services.latex import has_unwrapped_math, normalize_math
from services.profile import current_profile, profile_block
from services.prompting import ReplyFormatError, build_request, check_task, parse_json_reply

SLIDES_EXPECTED = 5

# Promises of outcomes and other claims the plan rules out. Matches are flagged, not removed.
_RISKY = re.compile(
    r"guarantee|100\s?%|\bA\+|get an? A\b|ace (?:the|your)|never fail|pass for sure|"
    r"保证|包过|提分|稳拿|必过|满分|拿A|轻松A|速成", re.I)
_PRICE = re.compile(r"\$\s?\d|\d+\s?(?:/\s?h(?:ou)?r|per hour|元|加币|刀|CAD)", re.I)
_CJK = re.compile(r"[一-鿿]")
# Soft (teaching-only) posts shouldn't advertise tutoring at all.
_TUTORING = re.compile(r"\btutor(?:ing)?\b|\blessons?\b|\bbook (?:a|your)\b|\bDM me\b|私信|辅导|预约|一对一|家教|补习|上课",
                       re.I)


class Slide(BaseModel):
    heading: str = ""
    body: str = Field(min_length=1)


class Draft(BaseModel):
    platform: Literal[tuple(PLATFORMS)]  # type: ignore[valid-type]
    language: Literal["zh", "en"]
    title_options: list[str] = Field(min_length=1)
    caption: str = Field(min_length=1)
    hashtags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    cta: str = ""
    slides: list[Slide] = Field(default_factory=list)


@dataclass
class DraftImport:
    drafts: list[dict] = field(default_factory=list)  # ready for Repository.add_posts
    rejected: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)  # the AI's own notes about your solution
    warnings: list[str] = field(default_factory=list)


def new_round_id() -> str:
    return new_id("round")


# ------------------------------------------------------------------ prompt
def build_drafts_prompt(repo: Repository, solution_id: str, *, platforms: list[str], cta_style: str,
                        include_rules: bool) -> str:
    if cta_style not in CTA_STYLES:
        raise ValueError(f"cta_style must be one of {CTA_STYLES}")
    if not platforms or set(platforms) - set(PLATFORMS):
        raise ValueError(f"platforms must be chosen from {PLATFORMS}")
    sol = next((s for s in repo.solutions_df().to_dict("records") if s["solution_id"] == solution_id), None)
    if sol is None:
        raise ValueError("Unknown solution.")
    question, solution = repo.get_question(sol["question_id"]), repo.get_solution(sol["question_id"])
    if not solution["approved"]:
        raise ValueError("Approve the mathematics in the Reasoning Workspace first.")

    tags = load_config().get("publishing", {}).get("base_hashtags", {})
    parts = [
        f"solution_id: {solution_id}",
        f"Platforms: {', '.join(platforms)}",
        f"CTA style: {cta_style}",
        "## Course context\n"
        f"Topic: {question['topic']} ({question['subject']})\n"
        f"Courses: {'; '.join(question['courses']) or '(not course-specific)'}",
        # Soft posts teach only, so the profile (services, contact) is left out on purpose.
        f"## Tutor profile\n{profile_block(current_profile(repo))}" if cta_style == "direct" else "",
        "## Suggested hashtags (use those that fit)\n" + "\n".join(
            f"{p}: {', '.join(tags.get(p, []))}" for p in platforms if tags.get(p)),
        f"## Problem\n{question['question_latex']}",
        f"## Tutor's approved solution\n{solution['raw_reasoning']}",
    ]
    for key, label in (("intuition", "Key intuition"), ("common_mistakes", "Common mistakes"),
                       ("alternate_methods", "Alternative methods")):
        if solution.get(key):
            parts.append(f"## {label}\n{solution[key]}")
    return build_request("DRAFTS", "\n\n".join(p for p in parts if p), include_rules=include_rules)


# ------------------------------------------------------------------ checks
def _clean_tags(tags: list[str]) -> list[str]:
    seen, out = set(), []
    for t in tags:
        t = re.sub(r"\s+", "", str(t).lstrip("#"))
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def check_post(platform: str, *, title: str, title_options: list[str], caption: str, hashtags: list[str],
               cta: str, slides: list[dict], cta_style: str | None = None) -> list[str]:
    """Warnings for one post. Used on import and again whenever you edit a post."""
    limits = load_config().get("publishing", {}).get("limits", {}).get(platform, {})
    flags = []
    if (n := limits.get("title_chars")) and len(title or "") > n:
        flags.append(f"Title is {len(title)} characters (limit {n}).")
    if (n := limits.get("caption_chars")) and len(caption) > n:
        flags.append(f"Caption is {len(caption)} characters (limit {n}).")
    if (n := limits.get("hashtags_max")) and len(hashtags) > n:
        flags.append(f"{len(hashtags)} hashtags (maximum {n}).")

    cjk_share = len(_CJK.findall(caption)) / max(len(caption), 1)
    if PLATFORM_LANGUAGE[platform] == "zh" and cjk_share < 0.2:
        flags.append("Caption doesn't look like Chinese.")
    if PLATFORM_LANGUAGE[platform] == "en" and cjk_share > 0.05:
        flags.append("Caption contains Chinese text on an English platform.")

    if "$" in caption or re.search(r"\\[A-Za-z]+", caption):
        flags.append("Caption contains LaTeX — captions are plain text on social platforms.")
    texts = [title, *title_options, caption, cta, *(s.get("heading", "") + " " + s.get("body", "") for s in slides)]
    risky = sorted({m.group(0) for t in texts for m in _RISKY.finditer(t or "")})
    if risky:
        flags.append(f"Possible promise of results: {', '.join(risky)} — rephrase.")
    if any(_PRICE.search(t or "") for t in texts):
        flags.append("Mentions a price — make sure it's your real rate.")
    if cta_style == "soft":
        ads = sorted({m.group(0) for t in [*texts, *hashtags] for m in _TUTORING.finditer(t or "")})
        if ads:
            flags.append(f"Soft (teaching-only) post mentions tutoring: {', '.join(ads)} — remove it.")

    if platform in ("xiaohongshu", "instagram"):
        if len(slides) != SLIDES_EXPECTED:
            flags.append(f"{len(slides)} slides (expected {SLIDES_EXPECTED}).")
        if any(has_unwrapped_math(s.get("body")) for s in slides):
            flags.append("Some slide maths is missing $ delimiters.")
    return flags


def parse_drafts(reply: str, *, expected_solution_id: str, platforms: list[str],
                 cta_style: str | None = None) -> DraftImport:
    data = parse_json_reply(reply)
    if not isinstance(data, dict) or not isinstance(data.get("drafts"), list):
        raise ReplyFormatError('Expected an object with a "drafts" list.')
    check_task(data, "DRAFTS")
    result = DraftImport(concerns=[str(c) for c in data.get("concerns") or [] if str(c).strip()])
    if data.get("solution_id") and data["solution_id"] != expected_solution_id:
        result.warnings.append("These drafts mention a different solution_id — check you pasted the right reply.")

    seen = set()
    for i, raw in enumerate(data["drafts"], start=1):
        try:
            d = Draft.model_validate(raw)
        except ValidationError as e:
            details = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
            result.rejected.append(f"Draft #{i}: {details}")
            continue
        if d.platform not in platforms:
            result.rejected.append(f"Draft #{i}: {d.platform} wasn't requested.")
            continue
        if d.platform in seen:
            result.rejected.append(f"Draft #{i}: a second {d.platform} draft (kept the first).")
            continue
        seen.add(d.platform)
        slides = [{"heading": s.heading.strip(), "body": normalize_math(s.body.strip())} for s in d.slides]
        if d.platform == "facebook":
            slides = []
        options = [t.strip() for t in d.title_options if t.strip()]
        post = {
            "platform": d.platform, "language": PLATFORM_LANGUAGE[d.platform],
            "title": options[0] if options else "", "title_options": options,
            "caption": d.caption.strip(), "hashtags": _clean_tags(d.hashtags),
            "keywords": [k.strip() for k in d.keywords if k.strip()], "cta": d.cta.strip(), "slides": slides,
        }
        post["flags"] = check_post(d.platform, cta_style=cta_style, **{
            k: post[k] for k in ("title", "title_options", "caption", "hashtags", "cta", "slides")})
        if d.language != PLATFORM_LANGUAGE[d.platform]:
            post["flags"].append(f"Draft was labelled '{d.language}' — expected '{PLATFORM_LANGUAGE[d.platform]}'.")
        if len(options) != 3:
            post["flags"].append(f"{len(options)} title options (expected 3).")
        result.drafts.append(post)
    for p in platforms:
        if p not in seen:
            result.warnings.append(f"No usable {p} draft in the reply.")
    return result


def copy_text(post: dict) -> str:
    """The text to paste into the platform: title, caption, hashtags."""
    tags = " ".join(f"#{t}" for t in post.get("hashtags") or [])
    return "\n\n".join(x for x in (post.get("title"), post.get("caption"), tags) if x)
