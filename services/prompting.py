"""Shared prompt machinery for every AI task (questions, math review, drafts).

All tasks share one rules document, so the tutor can save it once as ChatGPT Project
instructions; after that, each request only needs `Task: X` plus the data.
"""
from __future__ import annotations

import json
import re

from core.config import PROJECT_ROOT
from db.models import QUESTION_TYPES, TARGET_LEVELS
from db.repository import Repository

RULES_VERSION = "rules_v7"  # bump whenever any prompts/rules_*.md file changes meaningfully
PROMPTS = PROJECT_ROOT / "prompts"
TASK_FILES = {
    "QUESTIONS": "rules_questions.md",
    "MATH_REVIEW": "rules_math_review.md",
    "DRAFTS": "rules_drafts.md",
    "TOPICS": "rules_topics.md",
}


class ReplyFormatError(ValueError):
    pass


# ------------------------------------------------------------------ rules
def _fill(text: str) -> str:
    return (text.replace("<<RULES_VERSION>>", RULES_VERSION)
                .replace("<<QUESTION_TYPES>>", ", ".join(QUESTION_TYPES))
                .replace("<<TARGET_LEVELS>>", ", ".join(TARGET_LEVELS)))


def rules_text(tasks: list[str] | None = None) -> str:
    """The common rules plus the given task sections (all tasks by default)."""
    parts = [(PROMPTS / "rules_common.md").read_text(encoding="utf-8")]
    for task in tasks or list(TASK_FILES):
        parts.append((PROMPTS / TASK_FILES[task]).read_text(encoding="utf-8"))
    return _fill("\n\n".join(parts))


def saved_rules_version(repo: Repository) -> str | None:
    """Rules version the tutor saved in their ChatGPT Project (None = not using a Project)."""
    return repo.get_setting("chatgpt_rules_version")


def needs_full_rules(repo: Repository, is_manual: bool) -> bool:
    """API calls always carry the rules; manual prompts only if the Project copy is missing/outdated."""
    return not is_manual or saved_rules_version(repo) != RULES_VERSION


def build_request(task: str, body: str, *, include_rules: bool) -> str:
    if task not in TASK_FILES:
        raise ValueError(f"Unknown task {task}")
    if include_rules:
        header = rules_text([task]) + "\n\n---\n"
    else:
        header = (f"(Follow the general rules and the {task} section of this project's instructions, "
                  f"version {RULES_VERSION}, including the JSON code-block output format.)\n")
    return f"Task: {task}\n{header}\n{body.strip()}\n"


# ----------------------------------------------------------------- replies
_VALID_JSON_ESCAPES = set('"\\/bfnrtu')
# LaTeX commands that begin with a letter JSON would read as an escape (\f, \n, \t, \b, \r, \u).
# "\frac" written without doubling would silently become form-feed + "rac", so we repair these.
_LATEX_COLLISIONS = sorted("""
frac forall flat fbox footnotesize
nabla neq ne not notin nu ni nmid nleq ngeq nsubseteq newline noindent nolimits nexists
theta tau to times text textbf textit tan tanh tfrac tilde top triangle tbinom
beta bar binom big Big bigg Bigg bigl bigr bf boldsymbol bmatrix begin bullet backslash bot bigcup bigcap
rho right rightarrow Rightarrow rangle rfloor rceil rm rvert rVert
underline underbrace uparrow cup cap
""".split(), key=len, reverse=True)
_COLLISION_RE = re.compile(r"(%s)(?![A-Za-z])" % "|".join(_LATEX_COLLISIONS))


def repair_backslashes(text: str) -> str:
    """Double LaTeX backslashes that would otherwise be invalid or silently-wrong JSON escapes."""
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if nxt == "\\":  # already an escaped backslash
            out.append("\\\\")
            i += 2
        elif nxt == "u" and re.match(r"[0-9a-fA-F]{4}", text[i + 2:i + 6]):  # real \uXXXX escape
            out.append("\\u")
            i += 2
        elif nxt in _VALID_JSON_ESCAPES and not _COLLISION_RE.match(text, i + 1):
            out.append("\\" + nxt)
            i += 2
        else:  # LaTeX command or invalid escape: make it a literal backslash
            out.append("\\\\")
            i += 1
    return "".join(out)


def parse_json_reply(reply: str) -> dict | list:
    """Pull the JSON out of a pasted reply (tolerates code fences and surrounding text)."""
    text = (reply or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1).strip()
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        raise ReplyFormatError("No JSON found. Ask ChatGPT to reply with the JSON code block only.")
    end = max(text.rfind("}"), text.rfind("]"))
    try:
        return json.loads(repair_backslashes(text[start:end + 1]))
    except json.JSONDecodeError as e:
        raise ReplyFormatError(f"The JSON could not be read ({e.msg} at line {e.lineno}, column {e.colno}). "
                               "Ask ChatGPT to return valid JSON only.") from e


def check_task(data: dict, expected: str) -> None:
    """Catch a reply pasted into the wrong place (e.g. a DRAFTS reply into the review box)."""
    task = data.get("task")
    if task and str(task).upper() != expected:
        raise ReplyFormatError(f"This reply is for task {task}, but this box expects {expected}.")
