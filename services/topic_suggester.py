"""TOPICS task: read course documents (syllabus, textbook contents, calendar description) and
suggest new topics.

- Documents are saved per course (sources with full_text) so a textbook's contents can be reused.
- Suggested topics are added linked to the course. Documents are sources for the course, not for
  topics, so they don't inflate the "observed difficulty" ranking signal (which counts evidence of
  student difficulty, such as Stack Exchange questions).
- Difficulty is relative to the course; the course's existing ratings are sent as anchors.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pydantic import BaseModel, Field, ValidationError, field_validator

from collectors.page_reader import Document, clean_contents
from db.models import TARGET_LEVELS
from db.repository import Repository
from services.prompting import ReplyFormatError, build_request, check_task, parse_json_reply

DOC_TYPES = {
    "syllabus": "Syllabus / course page",
    "course_notes": "Course notes / outline (the course's own)",
    "textbook_contents": "Textbook contents (table of contents)",
    "calendar_description": "Calendar description",
    "other": "Other document",
}
CONTENTS_TYPES = {"course_notes", "textbook_contents"}  # tables of contents: dot leaders get stripped
EVIDENCE_TYPES = ["listed", "textbook_contents", "unmapped", "implied"]
UNTICKED_EVIDENCE = {"unmapped", "implied"}
MAX_TOTAL_CHARS = 24000  # shared by all documents in one request
DUPLICATE_RATIO = 0.85


class SuggestedTopic(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    subject: str = Field(min_length=2)
    target_level: str = "university_intro"
    difficulty_level: int = Field(default=3, ge=1, le=5)
    concept_description: str = ""
    student_difficulty: str = ""
    search_query: str = ""
    evidence_type: str = "implied"
    syllabus_evidence: str = ""

    @field_validator("target_level")
    @classmethod
    def _level(cls, v: str) -> str:
        return v if v in TARGET_LEVELS else "university_intro"

    @field_validator("evidence_type")
    @classmethod
    def _evidence(cls, v: str) -> str:
        return v if v in EVIDENCE_TYPES else "implied"


@dataclass
class TopicSuggestions:
    topics: list[dict] = field(default_factory=list)  # rows for the review table
    rejected: list[str] = field(default_factory=list)
    notes: str = ""
    warnings: list[str] = field(default_factory=list)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


# ---------------------------------------------------------------- documents
def save_document(repo: Repository, doc: Document, *, course_id: str, offering_id: str | None,
                  doc_type: str, title: str | None = None, scope: str | None = None) -> str:
    """Store a course document with its full text, for reuse in later suggestion rounds.
    `scope` (textbook contents): chapters/sections the course covers, e.g. "all" or "1-6, 9"."""
    if doc_type not in DOC_TYPES:
        raise ValueError(f"doc_type must be one of {list(DOC_TYPES)}")
    if doc_type in CONTENTS_TYPES:
        doc.text = clean_contents(doc.text)
    sid, _ = repo.add_source(
        source_type=doc_type, title=(title or doc.title)[:200], url=doc.url, course_id=course_id,
        offering_id=offering_id, content_excerpt=doc.text[:400],
        collection_method="public_page" if doc.url else "manual",
        notes=f"Course document ({doc.kind}, {len(doc.text.split())} words)")
    repo.set_source_text(sid, doc.text, scope)  # also fills in text for a document saved before
    return sid


def update_document(repo: Repository, source_id: str, *, doc_type: str, scope: str | None,
                    full_text: str) -> None:
    """Change a saved document's type and/or scope; switching to a contents type cleans its text."""
    if doc_type not in DOC_TYPES:
        raise ValueError(f"doc_type must be one of {list(DOC_TYPES)}")
    repo.set_document_type(source_id, doc_type,
                           clean_contents(full_text) if doc_type in CONTENTS_TYPES else None)
    repo.set_document_scope(source_id, scope)


def _fit_budget(texts: list[str], budget: int) -> list[str]:
    """Share the character budget fairly: short documents keep everything, long ones are cut."""
    allowance = [0] * len(texts)
    remaining, open_ = budget, list(range(len(texts)))
    while open_ and remaining > 0:
        share = remaining // len(open_)
        still_open = []
        for i in open_:
            need = len(texts[i]) - allowance[i]
            give = min(need, share)
            allowance[i] += give
            remaining -= give
            if allowance[i] < len(texts[i]):
                still_open.append(i)
        if len(still_open) == len(open_) and share == 0:
            break
        open_ = still_open
    return [t if a >= len(t) else t[:a] + f"\n[… truncated: first {a} of {len(t)} characters]"
            for t, a in zip(texts, allowance)]


# ------------------------------------------------------------------- prompt
def _scope_line(d: dict) -> str:
    if d["source_type"] == "course_notes":
        return "Scope: the course's own notes/outline, so everything in it is course coverage.\n"
    if d["source_type"] == "textbook_contents":
        return f"Chapters/sections in scope (from the tutor): {d.get('doc_scope') or 'not specified'}\n"
    return ""


def record_attachment(repo: Repository, *, course_id: str, offering_id: str | None, doc_type: str,
                      title: str, scope: str | None = None) -> str:
    """Keep a record that a document was attached directly in ChatGPT (its text isn't stored, so it
    doesn't appear among the reusable course documents)."""
    if doc_type not in DOC_TYPES:
        raise ValueError(f"doc_type must be one of {list(DOC_TYPES)}")
    sid, _ = repo.add_source(source_type=doc_type, title=f"Attached in ChatGPT: {title}"[:200], course_id=course_id,
                             offering_id=offering_id, collection_method="manual",
                             notes="Attached directly in ChatGPT; text not stored in the app"
                                   + (f" · in scope: {scope}" if scope else ""))
    return sid


def _attachment_block(i: int, a: dict, *, sent: bool = False) -> str:
    where = "sent with this message as a file" if sent else "attached to this chat message"
    return (f"## Document {i} ({where}): "
            f"{DOC_TYPES.get(a['source_type'], a['source_type'])} — {a['title']}\n{_scope_line(a)}"
            f"Its content is in the file {where}. Treat it exactly like the other documents, under the "
            "same evidence rules. If you cannot see it, say so in `notes`.")


def build_topics_prompt(repo: Repository, course_id: str, documents: list[dict], *, max_topics: int,
                        include_rules: bool, notes: str = "", attachments: list[dict] | None = None,
                        sent_files: list[dict] | None = None) -> str:
    """`documents`: dicts with title, source_type, url, full_text, doc_scope (rows of course_documents_df),
    included as text. `sent_files`: documents whose original file is sent with the API request.
    `attachments`: documents you attach in ChatGPT yourself. `notes`: the tutor's free-text notes for
    this round; they override anything else."""
    attachments, sent_files = attachments or [], sent_files or []
    if not documents and not attachments and not sent_files:
        raise ValueError("Choose at least one saved document, or describe the document you'll attach in ChatGPT.")
    course = repo.courses_df().set_index("course_id").loc[course_id]
    topics = repo.topics_df()
    in_course = topics[topics["course_ids"].apply(lambda ids: course_id in ids)].sort_values("difficulty_level")
    others = topics[~topics["topic_id"].isin(in_course["topic_id"])]
    anchors = "\n".join(f"- {r.name} (difficulty {r.difficulty_level})" for r in in_course.itertuples())

    texts = _fit_budget([d["full_text"] for d in documents], MAX_TOTAL_CHARS) if documents else []
    doc_blocks = [
        f"## Document {i}: {DOC_TYPES.get(d['source_type'], d['source_type'])} — {d['title']}"
        + (f" ({d['url']})" if d.get("url") else "") + f"\n{_scope_line(d)}<<<\n{text}\n>>>"
        for i, (d, text) in enumerate(zip(documents, texts), start=1)
    ] + [_attachment_block(i, a, sent=True) for i, a in enumerate(sent_files, start=len(documents) + 1)] \
      + [_attachment_block(i, a) for i, a in enumerate(attachments, start=len(documents) + len(sent_files) + 1)]
    body = "\n\n".join([
        f"course_id: {course_id}",
        f"Course: {course['university']} {course['course_code']} — {course['course_title']} (level {course['level']})",
        f"Suggest at most {max_topics} topics.",
        "## Existing topics in this course, with difficulty for this course (calibrate new ratings to these)\n"
        + (anchors or "(none yet)"),
        "## Other existing topics (also don't repeat these)\n" + ("\n".join(f"- {n}" for n in sorted(others["name"]))
                                                                  or "(none)"),
        "## Subjects in use\n" + ", ".join(repo.subjects()),
        *([f"## Tutor's notes about these documents\n{notes.strip()}"] if notes.strip() else []),
        *doc_blocks,
    ])
    return build_request("TOPICS", body, include_rules=include_rules)


# -------------------------------------------------------------------- reply
def parse_topic_suggestions(repo: Repository, reply: str, *, course_id: str) -> TopicSuggestions:
    data = parse_json_reply(reply)
    if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
        raise ReplyFormatError('Expected an object with a "topics" list.')
    check_task(data, "TOPICS")
    result = TopicSuggestions(notes=str(data.get("notes") or ""))
    if data.get("course_id") and data["course_id"] != course_id:
        result.warnings.append(f"The reply is for course {data['course_id']}, not {course_id}.")

    existing = {_norm(n): n for n in repo.topics_df()["name"]}
    seen: set[str] = set()
    for i, raw in enumerate(data["topics"], start=1):
        try:
            t = SuggestedTopic.model_validate(raw)
        except ValidationError as e:
            details = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
            result.rejected.append(f"#{i}: {details}")
            continue
        key = _norm(t.name)
        if key in seen:
            result.rejected.append(f"#{i} {t.name}: repeated in the reply.")
            continue
        seen.add(key)
        duplicate = next((name for norm, name in existing.items()
                          if norm == key or SequenceMatcher(None, norm, key).ratio() >= DUPLICATE_RATIO), None)
        result.topics.append({
            # unmapped/implied topics and look-alikes of existing topics start unticked
            "add": duplicate is None and t.evidence_type not in UNTICKED_EVIDENCE, **t.model_dump(),
            "search_query": re.sub(r"[^\w\s]", " ", t.search_query).strip(),
            "similar_to": duplicate or "",
        })
    return result


def add_suggested_topics(repo: Repository, rows: list[dict], *, course_id: str) -> list[str]:
    ids = []
    for row in rows:
        if not row.get("add"):
            continue
        ids.append(repo.add_topic(
            row["name"], row["subject"], [course_id], target_level=row["target_level"],
            difficulty_level=int(row["difficulty_level"]), concept_description=row.get("concept_description") or None,
            student_difficulty=row.get("student_difficulty") or None, search_query=row.get("search_query") or None))
    return ids
