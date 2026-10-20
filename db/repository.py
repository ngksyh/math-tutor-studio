"""All database reads and writes. UI code calls these instead of writing SQL."""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload, sessionmaker

from db.migrations import prepare_schema
from db.models import (
    COLLECTION_METHODS,
    CORE_TABLES,
    POST_STATUSES,
    REVIEW_STATUSES,
    SELECTION_STATUSES,
    SUBJECTS,
    TARGET_LEVELS,
    TERM_SEASONS,
    AppSetting,
    Base,
    Course,
    CourseOffering,
    Post,
    Question,
    ResearchRun,
    Solution,
    SolutionRevision,
    Source,
    Topic,
    TopicScore,
    utcnow,
)

COURSE_EDITABLE = ["university", "course_code", "course_title", "level", "priority", "active",
                   "calendar_url"]
OFFERING_EDITABLE = ["term_year", "term_season", "section", "instructor", "course_url", "syllabus_url",
                     "exam_notes", "monitor", "last_verified_at", "notes"]
TOPIC_EDITABLE = ["name", "subject", "concept_description", "difficulty_level", "target_level",
                  "student_difficulty", "search_query", "active"]
QUESTION_COLUMNS = ["question_id", "topic_id", "title", "question_latex", "question_plaintext",
                    "question_type", "difficulty", "expected_time_min", "target_audience",
                    "proposed_solution", "teaching_insight", "source_ids", "batch_id", "review_status",
                    "selection_status", "generation_model", "prompt_version", "created_at"]
SOLUTION_COLUMNS = ["solution_id", "question_id", "origin", "raw_reasoning", "revised_reasoning", "intuition",
                    "common_mistakes", "alternate_methods", "math_review_notes", "approved", "created_at",
                    "updated_at"]
POST_COLUMNS = ["post_id", "solution_id", "platform", "language", "title", "title_options", "caption",
                "hashtags", "keywords", "cta", "slides", "flags", "status", "draft_round", "cta_style",
                "use_question_card",
                "asset_directory", "scheduled_for", "published_at", "published_url", "created_at"]
POST_EDITABLE = {"title", "caption", "hashtags", "keywords", "cta", "slides", "flags", "status", "use_question_card",
                 "asset_directory", "scheduled_for", "published_at", "published_url"}
SOURCE_EDITABLE = ["excluded", "notes"]


class ValidationError(ValueError):
    pass


def _clean(value: Any) -> Any:
    """Convert pandas NaN/NaT to None and Timestamps to datetimes."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


def make_course_id(university: str, course_code: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", f"{university}_{course_code}".lower()).strip("_")


def term_label(year: int, season: str, section: str | None = "") -> str:
    label = f"{year} {season}"
    return f"{label} · {section}" if section else label


def content_hash(url: str | None, excerpt: str | None, title: str) -> str:
    """Fingerprint for deduplication: same URL + same excerpt is the same source."""
    norm_url = (url or "").strip().lower().rstrip("/")
    norm_text = re.sub(r"\s+", " ", (excerpt or "")).strip().lower()
    if not norm_url and not norm_text:
        norm_text = title.strip().lower()
    return hashlib.sha256(f"{norm_url}|{norm_text}".encode("utf-8")).hexdigest()


def create_db_engine(db_path: Path | str) -> Engine:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # lets the scheduler and the UI coexist
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    prepare_schema(engine)
    return engine


class Repository:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.engine = create_db_engine(self.db_path)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    # ------------------------------------------------------------------ courses
    def courses_df(self, active_only: bool = False) -> pd.DataFrame:
        with self.session() as s:
            stmt = (select(Course).options(selectinload(Course.offerings))
                    .order_by(Course.priority.desc(), Course.course_id))
            if active_only:
                stmt = stmt.where(Course.active.is_(True))
            rows = [
                {
                    "course_id": c.course_id,
                    **{col: getattr(c, col) for col in COURSE_EDITABLE},
                    "offerings": len(c.offerings),
                }
                for c in s.scalars(stmt)
            ]
        return pd.DataFrame(rows, columns=["course_id", *COURSE_EDITABLE, "offerings"])

    def add_course(self, university: str, course_code: str, course_title: str, level: int = 100,
                   priority: int = 3, calendar_url: str | None = None) -> str:
        if not university.strip() or not course_code.strip() or not course_title.strip():
            raise ValidationError("University, course code and title are required.")
        if not 1 <= priority <= 5:
            raise ValidationError("Priority must be between 1 and 5.")
        course_id = make_course_id(university, course_code)
        with self.session() as s:
            if s.get(Course, course_id):
                raise ValidationError(f"Course {course_id} already exists.")
            s.add(Course(course_id=course_id, university=university.strip(),
                         course_code=course_code.strip().upper(), course_title=course_title.strip(),
                         level=level, priority=priority, calendar_url=(calendar_url or "").strip() or None))
        return course_id

    def update_courses(self, df: pd.DataFrame) -> int:
        """Save edits from the course table editor. Returns number of fields changed."""
        changed = 0
        with self.session() as s:
            for row in df.to_dict("records"):
                course = s.get(Course, row["course_id"])
                if course is None:
                    continue
                for col in COURSE_EDITABLE:
                    if col not in row:
                        continue
                    new = _clean(row[col])
                    if col == "priority" and new is not None and not 1 <= int(new) <= 5:
                        raise ValidationError(f"{row['course_id']}: priority must be 1–5.")
                    if col in ("university", "course_code", "course_title") and not (new or "").strip():
                        raise ValidationError(f"{row['course_id']}: {col} cannot be empty.")
                    if getattr(course, col) != new:
                        setattr(course, col, new)
                        changed += 1
        return changed

    def delete_course(self, course_id: str) -> None:
        """Deletes the course and its offerings; sources and topics are kept (links are removed)."""
        with self.session() as s:
            if (course := s.get(Course, course_id)) is not None:
                s.delete(course)

    # ---------------------------------------------------------------- offerings
    def offerings_df(self, course_id: str | None = None) -> pd.DataFrame:
        with self.session() as s:
            stmt = select(CourseOffering).options(selectinload(CourseOffering.course))
            if course_id:
                stmt = stmt.where(CourseOffering.course_id == course_id)
            rows = [
                {
                    "offering_id": o.offering_id,
                    "course_id": o.course_id,
                    "course": f"{o.course.university} {o.course.course_code}",
                    **{col: getattr(o, col) for col in OFFERING_EDITABLE},
                    "term": term_label(o.term_year, o.term_season, o.section),
                }
                for o in s.scalars(stmt)
            ]
        df = pd.DataFrame(rows, columns=["offering_id", "course_id", "course", *OFFERING_EDITABLE, "term"])
        if not df.empty:  # newest term first within each course
            order = {t: i for i, t in enumerate(TERM_SEASONS)}
            df = (df.assign(_season=df["term_season"].map(order))
                    .sort_values(["course", "term_year", "_season"], ascending=[True, False, False])
                    .drop(columns="_season").reset_index(drop=True))
        return df

    @staticmethod
    def _validate_offering_fields(fields: dict[str, Any]) -> None:
        if "term_year" in fields and not 1950 <= int(fields["term_year"]) <= 2100:
            raise ValidationError("Term year must be between 1950 and 2100.")
        if "term_season" in fields and fields["term_season"] not in TERM_SEASONS:
            raise ValidationError(f"Term season must be one of {TERM_SEASONS}.")

    def add_offering(self, course_id: str, term_year: int, term_season: str, **fields: Any) -> str:
        fields = {k: ((v.strip() or None) if isinstance(v, str) else v) for k, v in fields.items()}
        fields["section"] = fields.get("section") or ""
        self._validate_offering_fields({"term_year": term_year, "term_season": term_season})
        with self.session() as s:
            if s.get(Course, course_id) is None:
                raise ValidationError(f"Unknown course {course_id}.")
            duplicate = s.scalar(select(CourseOffering).where(
                CourseOffering.course_id == course_id, CourseOffering.term_year == term_year,
                CourseOffering.term_season == term_season, CourseOffering.section == fields["section"]))
            if duplicate:
                raise ValidationError("That term/section already exists for this course.")
            offering = CourseOffering(course_id=course_id, term_year=int(term_year),
                                      term_season=term_season, **fields)
            s.add(offering)
            s.flush()
            return offering.offering_id

    def update_offerings(self, df: pd.DataFrame) -> int:
        changed = 0
        with self.session() as s:
            for row in df.to_dict("records"):
                offering = s.get(CourseOffering, row["offering_id"])
                if offering is None:
                    continue
                updates = {col: _clean(row[col]) for col in OFFERING_EDITABLE if col in row}
                if "section" in updates:
                    updates["section"] = updates["section"] or ""
                self._validate_offering_fields(updates)
                for col, new in updates.items():
                    if getattr(offering, col) != new:
                        setattr(offering, col, new)
                        changed += 1
        return changed

    def delete_offering(self, offering_id: str) -> None:
        with self.session() as s:
            if (offering := s.get(CourseOffering, offering_id)) is not None:
                s.delete(offering)

    def course_scope_choices(self) -> dict[tuple[str, str | None], str]:
        """Options for 'which course/term is this about': each course, then each of its offerings."""
        choices: dict[tuple[str, str | None], str] = {}
        offerings = self.offerings_df()
        for c in self.courses_df().itertuples():
            name = f"{c.university} {c.course_code}"
            choices[(c.course_id, None)] = f"{name} (any term)"
            for o in offerings[offerings["course_id"] == c.course_id].itertuples():
                choices[(c.course_id, o.offering_id)] = f"{name} — {o.term}"
        return choices

    # ------------------------------------------------------------------- topics
    def subjects(self) -> list[str]:
        """Default subjects plus any custom ones already in use."""
        with self.session() as s:
            used = set(s.scalars(select(Topic.subject).distinct()))
        return SUBJECTS + sorted(used - set(SUBJECTS))

    def topics_df(self, include_inactive: bool = True) -> pd.DataFrame:
        with self.session() as s:
            stmt = select(Topic).options(selectinload(Topic.courses), selectinload(Topic.sources))
            stmt = stmt.order_by(Topic.subject, Topic.name)
            if not include_inactive:
                stmt = stmt.where(Topic.active.is_(True))
            rows = [
                {
                    "topic_id": t.topic_id,
                    **{col: getattr(t, col) for col in TOPIC_EDITABLE},
                    "course_ids": sorted(c.course_id for c in t.courses),
                    "courses": ", ".join(sorted(f"{c.university} {c.course_code}" for c in t.courses)),
                    "source_count": len(t.sources),
                    "last_used_at": t.last_used_at,
                }
                for t in s.scalars(stmt)
            ]
        cols = ["topic_id", *TOPIC_EDITABLE, "course_ids", "courses", "source_count", "last_used_at"]
        return pd.DataFrame(rows, columns=cols)

    @staticmethod
    def _validate_topic_fields(fields: dict[str, Any]) -> None:
        if "name" in fields and not (fields["name"] or "").strip():
            raise ValidationError("Topic name is required.")
        if "subject" in fields and not (fields["subject"] or "").strip():
            raise ValidationError("Subject is required.")
        if "target_level" in fields and fields["target_level"] not in TARGET_LEVELS:
            raise ValidationError(f"target_level must be one of {TARGET_LEVELS}.")
        if "difficulty_level" in fields and not 1 <= int(fields["difficulty_level"]) <= 5:
            raise ValidationError("difficulty_level must be 1–5.")

    def add_topic(self, name: str, subject: str, course_ids: Iterable[str] = (), **fields: Any) -> str:
        fields = {"name": (name or "").strip(), "subject": (subject or "").strip(), **fields}
        self._validate_topic_fields(fields)
        with self.session() as s:
            topic = Topic(**fields)
            topic.courses = [c for cid in course_ids if (c := s.get(Course, cid))]
            s.add(topic)
            s.flush()
            return topic.topic_id

    def update_topics(self, df: pd.DataFrame) -> int:
        changed = 0
        with self.session() as s:
            for row in df.to_dict("records"):
                topic = s.get(Topic, row["topic_id"])
                if topic is None:
                    continue
                updates = {col: _clean(row[col]) for col in TOPIC_EDITABLE if col in row}
                self._validate_topic_fields(updates)
                for col, new in updates.items():
                    if getattr(topic, col) != new:
                        setattr(topic, col, new)
                        changed += 1
        return changed

    def set_topic_courses(self, topic_id: str, course_ids: Iterable[str]) -> None:
        with self.session() as s:
            topic = s.get(Topic, topic_id)
            if topic is None:
                raise ValidationError(f"Unknown topic {topic_id}.")
            topic.courses = [c for cid in course_ids if (c := s.get(Course, cid))]

    # ------------------------------------------------------------------ sources
    def add_source(self, *, source_type: str, title: str, url: str | None = None,
                   course_id: str | None = None, offering_id: str | None = None,
                   content_excerpt: str | None = None, published_at: datetime | None = None,
                   engagement_count: int | None = None, license: str | None = None,
                   platform_id: str | None = None, collection_method: str = "manual",
                   notes: str | None = None, topic_ids: Iterable[str] = ()) -> tuple[str, bool]:
        """Insert a source. Returns (source_id, created); created=False means it was a duplicate."""
        if not title.strip():
            raise ValidationError("Title is required.")
        if collection_method not in COLLECTION_METHODS:
            raise ValidationError(f"collection_method must be one of {COLLECTION_METHODS}.")
        digest = content_hash(url, content_excerpt, title)
        with self.session() as s:
            existing = s.scalar(select(Source).where(Source.content_hash == digest))
            if existing:
                return existing.source_id, False
            if offering_id:
                offering = s.get(CourseOffering, offering_id)
                if offering is None:
                    raise ValidationError(f"Unknown offering {offering_id}.")
                course_id = offering.course_id
            src = Source(source_type=source_type, title=title.strip(), url=(url or "").strip() or None,
                         course_id=course_id or None, offering_id=offering_id or None,
                         content_excerpt=content_excerpt or None, published_at=published_at,
                         engagement_count=engagement_count, license=license or None,
                         platform_id=platform_id or None, collection_method=collection_method,
                         notes=notes or None, content_hash=digest)
            src.topics = [t for tid in topic_ids if (t := s.get(Topic, tid))]
            s.add(src)
            s.flush()
            return src.source_id, True

    def sources_df(self, include_excluded: bool = True) -> pd.DataFrame:
        with self.session() as s:
            terms = {o.offering_id: term_label(o.term_year, o.term_season, o.section)
                     for o in s.scalars(select(CourseOffering))}
            stmt = select(Source).options(selectinload(Source.topics)).order_by(Source.fetched_at.desc())
            if not include_excluded:
                stmt = stmt.where(Source.excluded.is_(False))
            rows = [
                {
                    "source_id": src.source_id,
                    "source_type": src.source_type,
                    "title": src.title,
                    "url": src.url,
                    "course_id": src.course_id,
                    "offering_id": src.offering_id,
                    "term": terms.get(src.offering_id),
                    "published_at": src.published_at,
                    "fetched_at": src.fetched_at,
                    "engagement_count": src.engagement_count,
                    "license": src.license,
                    "collection_method": src.collection_method,
                    "collection_status": src.collection_status,
                    "excluded": src.excluded,
                    "notes": src.notes,
                    "content_excerpt": src.content_excerpt,
                    "topic_ids": sorted(t.topic_id for t in src.topics),
                    "topics": ", ".join(sorted(t.name for t in src.topics)),
                }
                for src in s.scalars(stmt)
            ]
        cols = ["source_id", "source_type", "title", "url", "course_id", "offering_id", "term",
                "published_at", "fetched_at", "engagement_count", "license", "collection_method",
                "collection_status", "excluded", "notes", "content_excerpt", "topic_ids", "topics"]
        return pd.DataFrame(rows, columns=cols)

    def update_sources(self, df: pd.DataFrame) -> int:
        changed = 0
        with self.session() as s:
            for row in df.to_dict("records"):
                src = s.get(Source, row["source_id"])
                if src is None:
                    continue
                for col in SOURCE_EDITABLE:
                    if col in row and getattr(src, col) != (new := _clean(row[col])):
                        setattr(src, col, new)
                        changed += 1
        return changed

    def set_source_text(self, source_id: str, full_text: str, doc_scope: str | None = None) -> None:
        with self.session() as s:
            src = s.get(Source, source_id)
            if src is None:
                raise ValidationError(f"Unknown source {source_id}.")
            src.full_text = full_text
            if doc_scope is not None:
                src.doc_scope = doc_scope.strip() or None

    def set_document_type(self, source_id: str, source_type: str, full_text: str | None = None) -> None:
        with self.session() as s:
            src = s.get(Source, source_id)
            if src is None:
                raise ValidationError(f"Unknown source {source_id}.")
            src.source_type = source_type
            if full_text is not None:
                src.full_text = full_text

    def set_document_scope(self, source_id: str, doc_scope: str | None) -> None:
        """Which chapters/sections of a textbook the course covers ("all", "1-6, 9")."""
        with self.session() as s:
            src = s.get(Source, source_id)
            if src is None:
                raise ValidationError(f"Unknown source {source_id}.")
            src.doc_scope = (doc_scope or "").strip() or None

    def course_documents_df(self, course_id: str) -> pd.DataFrame:
        """Saved course documents (syllabus, textbook contents, …) with reusable text and/or an
        uploaded original file."""
        cols = ["source_id", "source_type", "title", "url", "term", "doc_scope", "words", "file_path", "fetched_at",
                "full_text"]
        with self.session() as s:
            terms = {o.offering_id: term_label(o.term_year, o.term_season, o.section)
                     for o in s.scalars(select(CourseOffering).where(CourseOffering.course_id == course_id))}
            docs = s.scalars(select(Source).where(
                Source.course_id == course_id, Source.excluded.is_(False),
                (Source.full_text.is_not(None)) | (Source.file_path.is_not(None)),
            ).order_by(Source.fetched_at.desc()))
            rows = [{"source_id": d.source_id, "source_type": d.source_type, "title": d.title, "url": d.url,
                     "term": terms.get(d.offering_id), "doc_scope": d.doc_scope,
                     "words": len((d.full_text or "").split()), "file_path": d.file_path,
                     "fetched_at": d.fetched_at, "full_text": d.full_text or ""} for d in docs]
        return pd.DataFrame(rows, columns=cols)

    def set_source_file(self, source_id: str, file_path: str) -> None:
        with self.session() as s:
            src = s.get(Source, source_id)
            if src is None:
                raise ValidationError(f"Unknown source {source_id}.")
            src.file_path = file_path

    def set_source_scope(self, source_id: str, course_id: str | None, offering_id: str | None) -> None:
        """Set which course (and optionally which term) a source is about."""
        with self.session() as s:
            src = s.get(Source, source_id)
            if src is None:
                raise ValidationError(f"Unknown source {source_id}.")
            if offering_id:
                offering = s.get(CourseOffering, offering_id)
                if offering is None:
                    raise ValidationError(f"Unknown offering {offering_id}.")
                course_id = offering.course_id
            src.course_id, src.offering_id = course_id, offering_id

    def set_source_topics(self, source_id: str, topic_ids: Iterable[str]) -> None:
        with self.session() as s:
            src = s.get(Source, source_id)
            if src is None:
                raise ValidationError(f"Unknown source {source_id}.")
            src.topics = [t for tid in topic_ids if (t := s.get(Topic, tid))]

    def delete_source(self, source_id: str) -> None:
        with self.session() as s:
            if (src := s.get(Source, source_id)) is not None:
                s.delete(src)

    def upsert_platform_source(self, *, platform_id: str, topic_id: str | None, **fields: Any) -> tuple[str, bool]:
        """Insert a collector result, or refresh it if already stored (matched by platform_id).

        On refresh, engagement and excerpt are updated and the topic link is added; your
        notes, exclusion flag and course links are left alone. Returns (source_id, created).
        """
        with self.session() as s:
            src = s.scalar(select(Source).where(Source.platform_id == platform_id))
            created = src is None
            if created:
                digest = content_hash(fields.get("url"), fields.get("content_excerpt"), fields["title"])
                src = s.scalar(select(Source).where(Source.content_hash == digest))
                created = src is None
                if created:
                    src = Source(platform_id=platform_id, content_hash=digest, **fields)
                    s.add(src)
                else:
                    src.platform_id = platform_id
            if not created:
                for col in ("engagement_count", "content_excerpt", "title", "license"):
                    if fields.get(col) is not None:
                        setattr(src, col, fields[col])
                src.fetched_at = utcnow()
            if topic_id and (topic := s.get(Topic, topic_id)) is not None and topic not in src.topics:
                src.topics.append(topic)
            s.flush()
            return src.source_id, created

    # ------------------------------------------------------------ research runs
    def start_run(self, collector: str) -> str:
        with self.session() as s:
            run = ResearchRun(collector=collector)
            s.add(run)
            s.flush()
            return run.run_id

    def finish_run(self, run_id: str, *, status: str, requests_made: int, items_seen: int,
                   sources_added: int, quota_remaining: int | None, log: str) -> None:
        with self.session() as s:
            run = s.get(ResearchRun, run_id)
            run.status, run.finished_at = status, utcnow()
            run.requests_made, run.items_seen, run.sources_added = requests_made, items_seen, sources_added
            run.quota_remaining, run.log = quota_remaining, log

    def runs_df(self, limit: int = 20) -> pd.DataFrame:
        cols = ["run_id", "collector", "started_at", "finished_at", "status", "requests_made",
                "items_seen", "sources_added", "quota_remaining", "log"]
        with self.session() as s:
            runs = s.scalars(select(ResearchRun).order_by(ResearchRun.started_at.desc()).limit(limit))
            rows = [{c: getattr(r, c) for c in cols} for r in runs]
        return pd.DataFrame(rows, columns=cols)

    # ---------------------------------------------------------------- questions
    def questions_df(self, statuses: Iterable[str] | None = None) -> pd.DataFrame:
        with self.session() as s:
            stmt = select(Question).order_by(Question.created_at.desc())
            if statuses:
                stmt = stmt.where(Question.selection_status.in_(list(statuses)))
            rows = [{c: getattr(q, c) for c in QUESTION_COLUMNS} for q in s.scalars(stmt)]
            names = dict(s.execute(select(Topic.topic_id, Topic.name)).all())
        df = pd.DataFrame(rows, columns=QUESTION_COLUMNS)
        df["topic"] = df["topic_id"].map(names)
        return df

    def add_questions(self, questions: Iterable[dict[str, Any]], *, batch_id: str, generation_model: str,
                      prompt_version: str) -> list[str]:
        """Save validated candidate questions. AI solutions always start as 'unreviewed'."""
        ids = []
        with self.session() as s:
            for data in questions:
                q = Question(**data, batch_id=batch_id, generation_model=generation_model,
                             prompt_version=prompt_version, review_status="unreviewed",
                             selection_status="candidate")
                s.add(q)
                s.flush()
                ids.append(q.question_id)
        return ids

    def set_question_status(self, question_id: str, *, selection_status: str | None = None,
                            review_status: str | None = None) -> None:
        if selection_status and selection_status not in SELECTION_STATUSES:
            raise ValidationError(f"selection_status must be one of {SELECTION_STATUSES}.")
        if review_status and review_status not in REVIEW_STATUSES:
            raise ValidationError(f"review_status must be one of {REVIEW_STATUSES}.")
        with self.session() as s:
            q = s.get(Question, question_id)
            if q is None:
                raise ValidationError(f"Unknown question {question_id}.")
            if selection_status:
                q.selection_status = selection_status
                if selection_status == "selected" and (topic := s.get(Topic, q.topic_id)) is not None:
                    topic.last_used_at = utcnow()  # feeds the novelty score
            if review_status:
                q.review_status = review_status

    def update_question_text(self, question_id: str, **fields: str) -> None:
        """Edit a question's wording (title, question_latex, proposed_solution, teaching_insight)."""
        allowed = {"title", "question_latex", "question_plaintext", "proposed_solution", "teaching_insight"}
        if unknown := set(fields) - allowed:
            raise ValidationError(f"Cannot edit {sorted(unknown)}.")
        if "question_latex" in fields and len((fields["question_latex"] or "").strip()) < 10:
            raise ValidationError("The question text is too short.")
        with self.session() as s:
            q = s.get(Question, question_id)
            if q is None:
                raise ValidationError(f"Unknown question {question_id}.")
            for k, v in fields.items():
                setattr(q, k, v)

    def delete_question(self, question_id: str) -> None:
        with self.session() as s:
            if (q := s.get(Question, question_id)) is not None:
                s.delete(q)

    def get_question(self, question_id: str) -> dict[str, Any] | None:
        with self.session() as s:
            q = s.get(Question, question_id)
            if q is None:
                return None
            data = {c: getattr(q, c) for c in QUESTION_COLUMNS}
            topic = s.get(Topic, q.topic_id)
            data["topic"] = topic.name if topic else None
            data["subject"] = topic.subject if topic else None
            data["courses"] = sorted(f"{c.university} {c.course_code} ({c.course_title})"
                                     for c in topic.courses) if topic else []
            data["course_codes"] = sorted(c.course_code for c in topic.courses if c.active) if topic else []
            return data

    # ---------------------------------------------------------------- solutions
    def get_solution(self, question_id: str) -> dict[str, Any] | None:
        """The tutor's solution for a question (one per question)."""
        with self.session() as s:
            sol = s.scalar(select(Solution).where(Solution.question_id == question_id))
            return {c: getattr(sol, c) for c in SOLUTION_COLUMNS} if sol else None

    def save_solution(self, question_id: str, *, raw_reasoning: str, intuition: str | None = None,
                      common_mistakes: str | None = None, alternate_methods: str | None = None) -> tuple[str, bool]:
        """Create or update your solution; every change is kept as a revision.

        Editing the mathematics after approval withdraws the approval. Returns (solution_id, changed).
        """
        if not (raw_reasoning or "").strip():
            raise ValidationError("Your reasoning can't be empty.")
        fields = {"raw_reasoning": raw_reasoning, "intuition": intuition or None,
                  "common_mistakes": common_mistakes or None, "alternate_methods": alternate_methods or None}
        with self.session() as s:
            if s.get(Question, question_id) is None:
                raise ValidationError(f"Unknown question {question_id}.")
            sol = s.scalar(select(Solution).where(Solution.question_id == question_id))
            if sol is None:
                sol = Solution(question_id=question_id, **fields)
                s.add(sol)
                changed = True
            else:
                changed = any(getattr(sol, k) != v for k, v in fields.items())
                if changed:
                    for k, v in fields.items():
                        setattr(sol, k, v)
                    if sol.approved:
                        sol.approved = False
                        s.get(Question, question_id).review_status = "unreviewed"
            s.flush()
            if changed:
                s.add(SolutionRevision(solution_id=sol.solution_id, **fields))
            return sol.solution_id, changed

    def start_from_ai(self, question_id: str) -> str:
        """Verify mode: create the solution from the AI-proposed solution, for you to check and edit.
        The first revision is labelled as the AI draft; approval is still required before publishing."""
        with self.session() as s:
            q = s.get(Question, question_id)
            if q is None:
                raise ValidationError(f"Unknown question {question_id}.")
            if not (q.proposed_solution or "").strip():
                raise ValidationError("This question has no AI-proposed solution.")
            if s.scalar(select(Solution).where(Solution.question_id == question_id)) is not None:
                raise ValidationError("A solution already exists for this question.")
            fields = {"raw_reasoning": q.proposed_solution, "intuition": q.teaching_insight or None,
                      "common_mistakes": None, "alternate_methods": None}
            sol = Solution(question_id=question_id, origin="ai_verified", **fields)
            s.add(sol)
            s.flush()
            s.add(SolutionRevision(solution_id=sol.solution_id, author="ai", **fields))
            return sol.solution_id

    def revisions_df(self, solution_id: str) -> pd.DataFrame:
        cols = ["revision_id", "created_at", "author", "raw_reasoning", "intuition", "common_mistakes",
                "alternate_methods"]
        with self.session() as s:
            revs = s.scalars(select(SolutionRevision).where(SolutionRevision.solution_id == solution_id)
                             .order_by(SolutionRevision.created_at.desc()))
            rows = [{c: getattr(r, c) for c in cols} for r in revs]
        return pd.DataFrame(rows, columns=cols)

    def set_math_review(self, solution_id: str, notes: str, question_review_status: str) -> None:
        if question_review_status not in REVIEW_STATUSES:
            raise ValidationError(f"review_status must be one of {REVIEW_STATUSES}.")
        with self.session() as s:
            sol = s.get(Solution, solution_id)
            if sol is None:
                raise ValidationError(f"Unknown solution {solution_id}.")
            sol.math_review_notes = notes
            question = s.get(Question, sol.question_id)
            if not sol.approved:  # an approved solution stays verified
                question.review_status = question_review_status

    def set_solution_approved(self, solution_id: str, approved: bool) -> None:
        """Your final sign-off that the mathematics is correct (required before drafts)."""
        with self.session() as s:
            sol = s.get(Solution, solution_id)
            if sol is None:
                raise ValidationError(f"Unknown solution {solution_id}.")
            sol.approved = approved
            s.get(Question, sol.question_id).review_status = "verified" if approved else "unreviewed"

    def solutions_df(self, approved_only: bool = False) -> pd.DataFrame:
        cols = ["solution_id", "question_id", "title", "topic", "approved", "updated_at", "post_count"]
        with self.session() as s:
            stmt = select(Solution, Question, Topic).join(Question, Solution.question_id == Question.question_id) \
                .join(Topic, Question.topic_id == Topic.topic_id).order_by(Solution.updated_at.desc())
            if approved_only:
                stmt = stmt.where(Solution.approved.is_(True))
            counts = dict(s.execute(select(Post.solution_id, func.count()).group_by(Post.solution_id)).all())
            rows = [{"solution_id": sol.solution_id, "question_id": q.question_id, "title": q.title,
                     "topic": t.name, "approved": sol.approved, "updated_at": sol.updated_at,
                     "post_count": counts.get(sol.solution_id, 0)} for sol, q, t in s.execute(stmt)]
        return pd.DataFrame(rows, columns=cols)

    # -------------------------------------------------------------------- posts
    def add_posts(self, solution_id: str, drafts: Iterable[dict[str, Any]], *, draft_round: str,
                  cta_style: str) -> list[str]:
        ids = []
        with self.session() as s:
            if s.get(Solution, solution_id) is None:
                raise ValidationError(f"Unknown solution {solution_id}.")
            for d in drafts:
                # the stored question is in English, so the exact-question card defaults on for English posts
                d = {"use_question_card": d.get("language") == "en" and d.get("platform") != "facebook", **d}
                post = Post(solution_id=solution_id, draft_round=draft_round, cta_style=cta_style,
                            status="draft", **d)
                s.add(post)
                s.flush()
                ids.append(post.post_id)
        return ids

    def posts_df(self, solution_id: str | None = None) -> pd.DataFrame:
        with self.session() as s:
            stmt = select(Post).order_by(Post.created_at.desc())
            if solution_id:
                stmt = stmt.where(Post.solution_id == solution_id)
            rows = [{c: getattr(p, c) for c in POST_COLUMNS} for p in s.scalars(stmt)]
        return pd.DataFrame(rows, columns=POST_COLUMNS)

    def update_post(self, post_id: str, **fields: Any) -> None:
        if unknown := set(fields) - POST_EDITABLE:
            raise ValidationError(f"Cannot edit {sorted(unknown)}.")
        if "status" in fields and fields["status"] not in POST_STATUSES:
            raise ValidationError(f"status must be one of {POST_STATUSES}.")
        with self.session() as s:
            post = s.get(Post, post_id)
            if post is None:
                raise ValidationError(f"Unknown post {post_id}.")
            for k, v in fields.items():
                setattr(post, k, v)
            if post.status == "published" and post.published_at is None:
                post.published_at = utcnow()

    def delete_post(self, post_id: str) -> None:
        with self.session() as s:
            if (post := s.get(Post, post_id)) is not None:
                s.delete(post)

    def save_topic_scores(self, scores: pd.DataFrame, *, batch_id: str, version: str) -> None:
        cols = ["course_relevance", "observed_difficulty", "timeliness", "content_quality_potential",
                "novelty", "final_score", "evidence_missing"]
        with self.session() as s:
            for row in scores.to_dict("records"):
                s.add(TopicScore(batch_id=batch_id, topic_id=row["topic_id"], score_version=version,
                                 **{c: _clean(row[c]) for c in cols}))

    # ----------------------------------------------------------------- settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.session() as s:
            row = s.get(AppSetting, key)
            return row.value if row and row.value is not None else default

    def get_json_setting(self, key: str, default: Any = None) -> Any:
        raw = self.get_setting(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    def set_json_setting(self, key: str, value: Any) -> None:
        self.set_setting(key, None if value is None else json.dumps(value))

    def set_setting(self, key: str, value: str | None) -> None:
        with self.session() as s:
            row = s.get(AppSetting, key)
            if row is None:
                s.add(AppSetting(key=key, value=value))
            else:
                row.value = value
                row.updated_at = utcnow()

    # ------------------------------------------------------------------- backup
    def backup_bytes(self) -> bytes:
        """A complete, consistent copy of the database as one file.

        Reading data/tutoring.db directly is NOT enough: in WAL mode, recent changes live in
        tutoring.db-wal until a checkpoint. SQLite's own serialisation includes them.
        """
        import sqlite3

        with sqlite3.connect(self.db_path) as conn:
            return conn.serialize()

    def backup_to(self, target: Path | str) -> Path:
        """Write a complete copy of the database to `target` (e.g. before an upgrade or a move)."""
        import sqlite3

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        src, dst = sqlite3.connect(self.db_path), sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return target

    # -------------------------------------------------------------------- stats
    def table_counts(self) -> dict[str, int]:
        counts = {}
        with self.session() as s:
            for name in CORE_TABLES:
                table = Base.metadata.tables[name]
                counts[name] = s.scalar(select(func.count()).select_from(table)) or 0
        return counts
