"""Build the question-generation prompt and validate the AI's reply.

The same prompt and validator are used for both modes: pasted from ChatGPT (manual) or
returned by the OpenAI API. Nothing reaches the database without passing validate_reply().
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from core.config import PROJECT_ROOT, load_config
from db.models import QUESTION_TYPES, TARGET_LEVELS, new_id
from db.repository import Repository
from services.latex import normalize_math
from services.prompting import (  # noqa: F401 — re-exported for the UI
    RULES_VERSION,
    ReplyFormatError,
    build_request,
    check_task,
    parse_json_reply,
    rules_text,
    saved_rules_version,
)

PROMPT_VERSION = "questions_v2"  # recorded with each question
REQUEST_PATH = PROJECT_ROOT / "prompts" / "question_request.md"
DEFAULT_PREFERENCES = "(none — use your judgment)"
MAX_SOURCES_PER_TOPIC = 5
EXCERPT_CHARS = 300
DUPLICATE_RATIO = 0.88


class CandidateQuestion(BaseModel):
    topic_id: str
    title: str = Field(min_length=3, max_length=200)
    question_latex: str = Field(min_length=10)
    question_type: Literal[tuple(QUESTION_TYPES)]  # type: ignore[valid-type]
    difficulty: int = Field(ge=1, le=5)
    expected_time_min: int = Field(ge=1, le=180)
    target_audience: Literal[tuple(TARGET_LEVELS)]  # type: ignore[valid-type]
    proposed_solution: str = Field(min_length=1)
    teaching_insight: str = ""
    source_ids: list[str] = Field(default_factory=list)


@dataclass
class Rejection:
    index: int
    title: str
    reasons: list[str]


@dataclass
class ValidationReport:
    batch_id: str | None
    accepted: list[CandidateQuestion] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- prompt
def new_batch_id() -> str:
    return new_id("batch")


def _topic_block(repo: Repository, topic_ids: list[str]) -> str:
    topics = repo.topics_df().set_index("topic_id")
    sources = repo.sources_df(include_excluded=False)
    blocks = []
    for tid in topic_ids:
        t = topics.loc[tid]
        lines = [f"### {t['name']}  (topic_id: {tid})",
                 f"- Subject: {t['subject']} · level: {t['target_level']} · difficulty: {t['difficulty_level']}/5"]
        if t["courses"]:
            lines.append(f"- Courses: {t['courses']}")
        if t["concept_description"]:
            lines.append(f"- Concept: {t['concept_description']}")
        if t["student_difficulty"]:
            lines.append(f"- Typical difficulty (tutor's note): {t['student_difficulty']}")
        linked = sources[sources["topic_ids"].apply(lambda ids, tid=tid: tid in ids)]
        linked = linked.sort_values("engagement_count", ascending=False, na_position="last")
        linked = linked.head(MAX_SOURCES_PER_TOPIC)
        if linked.empty:
            lines.append("- Evidence: none collected yet (use teaching judgment; source_ids must be empty).")
        for s in linked.itertuples():
            excerpt = (s.content_excerpt or "")[:EXCERPT_CHARS].replace("\n", " ")
            votes = "" if pd.isna(s.engagement_count) else f", {int(s.engagement_count)} votes"
            lines.append(f"- Evidence {s.source_id}: \"{s.title}\" ({s.url or 'no link'}{votes}). {excerpt}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def current_preferences(repo: Repository) -> str:
    saved = repo.get_setting("question_preferences")
    return saved if saved is not None else load_config()["ai"].get("question_preferences", "")


def build_prompt(repo: Repository, topic_ids: list[str], n_questions: int, batch_id: str, *,
                 include_rules: bool = True, preferences: str = "") -> str:
    """The request for one batch. With include_rules=False it assumes ChatGPT already has the
    rules (saved as ChatGPT Project instructions) and sends only the data."""
    if not topic_ids:
        raise ValueError("Select at least one topic.")
    body = (REQUEST_PATH.read_text(encoding="utf-8")
            .replace("<<N_QUESTIONS>>", str(n_questions))
            .replace("<<BATCH_ID>>", batch_id)
            .replace("<<PREFERENCES>>", preferences.strip() or DEFAULT_PREFERENCES)
            .replace("<<TOPICS>>", _topic_block(repo, topic_ids)))
    return build_request("QUESTIONS", body, include_rules=include_rules)


# ---------------------------------------------------------------- parsing
def extract_json(reply: str) -> dict:
    data = parse_json_reply(reply)
    if isinstance(data, list):
        data = {"questions": data}
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ReplyFormatError('Expected an object with a "questions" list.')
    check_task(data, "QUESTIONS")
    return data


# ---------------------------------------------------------------- validation
def _normalize(text: str) -> str:
    text = re.sub(r"\\[A-Za-z]+|[${}\\^_]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def plaintext(latex: str) -> str:
    text = re.sub(r"\$\$?", "", latex)
    text = re.sub(r"\\(?:text|mathrm|mathbf|operatorname)\{([^}]*)\}", r"\1", text)
    return re.sub(r"\s+", " ", text.replace("\\", "")).strip()


def validate_reply(repo: Repository, reply: str, expected_batch_id: str | None = None) -> ValidationReport:
    data = extract_json(reply)
    report = ValidationReport(batch_id=data.get("batch_id"))
    if expected_batch_id and report.batch_id and report.batch_id != expected_batch_id:
        report.warnings.append(f"Reply is for batch {report.batch_id}, not the latest prompt "
                               f"({expected_batch_id}). It was still checked against your database.")

    topics = repo.topics_df()
    topic_ids = set(topics["topic_id"])
    sources = repo.sources_df()
    source_topics = dict(zip(sources["source_id"], sources["topic_ids"]))
    existing = [_normalize(q) for q in repo.questions_df()["question_latex"]]
    accepted_norm: list[str] = []

    for i, raw in enumerate(data["questions"], start=1):
        title = str(raw.get("title", f"question {i}")) if isinstance(raw, dict) else f"question {i}"
        try:
            q = CandidateQuestion.model_validate(raw)
        except ValidationError as e:
            reasons = [f"{'.'.join(map(str, err['loc'])) or 'item'}: {err['msg']}" for err in e.errors()]
            report.rejected.append(Rejection(i, title, reasons))
            continue
        reasons = []
        if q.topic_id not in topic_ids:
            reasons.append(f"unknown topic_id '{q.topic_id}'")
        for sid in q.source_ids:
            if sid not in source_topics:
                reasons.append(f"source '{sid}' does not exist (invented sources are not allowed)")
            elif q.topic_id not in source_topics[sid]:
                reasons.append(f"source '{sid}' is not evidence for this topic")
        norm = _normalize(q.question_latex)
        if any(SequenceMatcher(None, norm, other).ratio() >= DUPLICATE_RATIO for other in existing):
            reasons.append("near-duplicate of a question already in your database")
        elif any(SequenceMatcher(None, norm, other).ratio() >= DUPLICATE_RATIO for other in accepted_norm):
            reasons.append("near-duplicate of another question in this reply")
        if reasons:
            report.rejected.append(Rejection(i, q.title, reasons))
        else:
            report.accepted.append(q)
            accepted_norm.append(norm)
    return report


TEXT_FIELDS = ("title", "question_latex", "proposed_solution", "teaching_insight")


def save_accepted(repo: Repository, report: ValidationReport, *, batch_id: str, generation_model: str) -> list[str]:
    rows = []
    for q in report.accepted:
        data = q.model_dump()
        data.update({f: normalize_math(data[f]) for f in TEXT_FIELDS})  # \( \) -> $ $ etc.
        data["question_plaintext"] = plaintext(data["question_latex"])
        rows.append(data)
    return repo.add_questions(rows, batch_id=report.batch_id or batch_id, generation_model=generation_model,
                              prompt_version=PROMPT_VERSION)
