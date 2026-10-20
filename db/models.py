"""SQLAlchemy models. SQLite is the source of truth; pandas DataFrames are views of it."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# --- Allowed values (validated in the repository and offered in the UI) ---
TARGET_LEVELS = ["university_intro", "intro_proofs", "advanced_undergrad", "gre_math_subject"]
# Default subjects; any new subject typed in the UI is accepted and offered from then on.
SUBJECTS = [
    "Calculus",
    "Linear Algebra",
    "Differential Equations",
    "Probability",
    "Statistics",
    "Proof Writing",
    "Real Analysis",
    "Complex Analysis",
    "Abstract Algebra",
    "Topology",
    "Partial Differential Equations",
    "Differential Geometry",
    "Differential Topology",
    "Numerical Analysis",
    "Discrete Mathematics",
    "GRE Mathematics",
    "Other",
]
TERM_SEASONS = ["Winter", "Spring", "Summer", "Fall", "Full year"]  # chronological within a year
COLLECTION_METHODS = ["manual", "api", "public_page", "csv_import"]
COLLECTION_STATUSES = ["ok", "failed"]
QUESTION_TYPES = [
    "computational",
    "conceptual",
    "proof",
    "counterexample",
    "multiple_choice",
    "gre_math_subject",
]
REVIEW_STATUSES = ["unreviewed", "ai_checked", "flagged", "verified"]
SELECTION_STATUSES = ["candidate", "favorite", "selected", "rejected"]
PLATFORMS = ["xiaohongshu", "instagram", "facebook"]
PLATFORM_LANGUAGE = {"xiaohongshu": "zh", "instagram": "en", "facebook": "en"}
CTA_STYLES = ["soft", "direct"]
POST_STATUSES = ["draft", "needs_review", "approved", "published", "archived", "rejected"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Base(DeclarativeBase):
    pass


# --- Link tables ---
course_topics = Table(
    "course_topics",
    Base.metadata,
    Column("course_id", ForeignKey("courses.course_id", ondelete="CASCADE"), primary_key=True),
    Column("topic_id", ForeignKey("topics.topic_id", ondelete="CASCADE"), primary_key=True),
)

source_topics = Table(
    "source_topics",
    Base.metadata,
    Column("source_id", ForeignKey("sources.source_id", ondelete="CASCADE"), primary_key=True),
    Column("topic_id", ForeignKey("topics.topic_id", ondelete="CASCADE"), primary_key=True),
)


# --- Core tables ---
class Course(Base):
    """A course as it stays from year to year. Term-specific pages live in CourseOffering."""

    __tablename__ = "courses"

    course_id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. uoft_mat137
    university: Mapped[str] = mapped_column(String)
    course_code: Mapped[str] = mapped_column(String)
    course_title: Mapped[str] = mapped_column(String)
    level: Mapped[int] = mapped_column(Integer, default=100)
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1–5
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    calendar_url: Mapped[str | None] = mapped_column(String, nullable=True)  # official calendar entry
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    topics: Mapped[list["Topic"]] = relationship(secondary=course_topics, back_populates="courses")
    offerings: Mapped[list["CourseOffering"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class CourseOffering(Base):
    """One term of a course (e.g. MAT137 2026 Fall, section LEC0101) with its own pages."""

    __tablename__ = "course_offerings"
    __table_args__ = (UniqueConstraint("course_id", "term_year", "term_season", "section"),)

    offering_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("off"))
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.course_id", ondelete="CASCADE"))
    term_year: Mapped[int] = mapped_column(Integer)
    term_season: Mapped[str] = mapped_column(String)
    section: Mapped[str] = mapped_column(String, default="")  # "" = whole course / all sections
    instructor: Mapped[str | None] = mapped_column(String, nullable=True)
    course_url: Mapped[str | None] = mapped_column(String, nullable=True)
    syllabus_url: Mapped[str | None] = mapped_column(String, nullable=True)
    exam_notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # known midterm/exam dates
    monitor: Mapped[bool] = mapped_column(Boolean, default=False)  # re-check these pages for changes
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    course: Mapped[Course] = relationship(back_populates="offerings")


class Topic(Base):
    __tablename__ = "topics"

    topic_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("top"))
    name: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    concept_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty_level: Mapped[int] = mapped_column(Integer, default=3)  # 1–5
    target_level: Mapped[str] = mapped_column(String, default="university_intro")
    student_difficulty: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_query: Mapped[str | None] = mapped_column(String, nullable=True)  # Stack Exchange search; None = name
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    courses: Mapped[list[Course]] = relationship(secondary=course_topics, back_populates="topics")
    sources: Mapped[list["Source"]] = relationship(secondary=source_topics, back_populates="topics")


class Source(Base):
    """A retrieved resource or discussion — evidence, not necessarily a misconception."""

    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("src"))
    source_type: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    platform_id: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. SE question id
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("courses.course_id", ondelete="SET NULL"), nullable=True
    )
    offering_id: Mapped[str | None] = mapped_column(
        ForeignKey("course_offerings.offering_id", ondelete="SET NULL"), nullable=True
    )
    content_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whole text of a course document (syllabus, textbook contents, …) kept for reuse by the
    # topic suggester. Empty for ordinary evidence such as Stack Exchange posts.
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For textbook contents: which chapters/sections the course covers ("all", "1–6, 9"); None = not specified
    doc_scope: Mapped[str | None] = mapped_column(String, nullable=True)
    # Uploaded original (PDF / image / text), relative to the project folder, e.g. data/imports/src_x.pdf
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    engagement_count: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = unknown, not zero
    license: Mapped[str | None] = mapped_column(String, nullable=True)
    content_hash: Mapped[str] = mapped_column(String, unique=True)
    collection_method: Mapped[str] = mapped_column(String, default="manual")
    collection_status: Mapped[str] = mapped_column(String, default="ok")
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    topics: Mapped[list[Topic]] = relationship(secondary=source_topics, back_populates="sources")


class Question(Base):
    __tablename__ = "questions"

    question_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("q"))
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.topic_id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    question_latex: Mapped[str] = mapped_column(Text)
    question_plaintext: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_type: Mapped[str] = mapped_column(String)
    difficulty: Mapped[int] = mapped_column(Integer, default=3)
    expected_time_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_audience: Mapped[str] = mapped_column(String, default="university_intro")
    proposed_solution: Mapped[str | None] = mapped_column(Text, nullable=True)  # AI-generated, unverified
    teaching_insight: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    batch_id: Mapped[str | None] = mapped_column(String, nullable=True)  # one generation/import round
    review_status: Mapped[str] = mapped_column(String, default="unreviewed")
    selection_status: Mapped[str] = mapped_column(String, default="candidate")
    generation_model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Solution(Base):
    """Your own mathematics. Kept separate from AI-proposed solutions."""

    __tablename__ = "solutions"

    solution_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("sol"))
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.question_id", ondelete="CASCADE"))
    raw_reasoning: Mapped[str] = mapped_column(Text)  # the solution text; AI never overwrites it
    # "own" = you wrote it; "ai_verified" = started from the AI solution, which you then checked/edited
    origin: Mapped[str] = mapped_column(String, default="own")
    revised_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    intuition: Mapped[str | None] = mapped_column(Text, nullable=True)
    common_mistakes: Mapped[str | None] = mapped_column(Text, nullable=True)
    alternate_methods: Mapped[str | None] = mapped_column(Text, nullable=True)
    math_review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SolutionRevision(Base):
    """Snapshot of a solution at every save, so nothing you wrote is ever lost."""

    __tablename__ = "solution_revisions"

    revision_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("rev"))
    solution_id: Mapped[str] = mapped_column(ForeignKey("solutions.solution_id", ondelete="CASCADE"))
    raw_reasoning: Mapped[str] = mapped_column(Text)
    intuition: Mapped[str | None] = mapped_column(Text, nullable=True)
    common_mistakes: Mapped[str | None] = mapped_column(Text, nullable=True)
    alternate_methods: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(String, default="you")  # "you" or "ai" (the imported AI draft)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Post(Base):
    __tablename__ = "posts"

    post_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("post"))
    solution_id: Mapped[str] = mapped_column(ForeignKey("solutions.solution_id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String)
    language: Mapped[str] = mapped_column(String)  # zh / en
    title: Mapped[str | None] = mapped_column(Text, nullable=True)  # the chosen title
    title_options: Mapped[list] = mapped_column(JSON, default=list)  # searchable / curiosity / exam
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    cta: Mapped[str | None] = mapped_column(Text, nullable=True)
    slides: Mapped[list] = mapped_column(JSON, default=list)  # [{"heading": ..., "body": ...}]
    flags: Mapped[list] = mapped_column(JSON, default=list)  # automatic warnings (length, claims, ...)
    draft_round: Mapped[str | None] = mapped_column(String, nullable=True)  # one generation round
    cta_style: Mapped[str | None] = mapped_column(String, nullable=True)  # soft / direct
    # Slide 2 shows the exact stored question on a decorated card instead of the AI-written problem slide
    use_question_card: Mapped[bool] = mapped_column(Boolean, default=False)
    asset_directory: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="draft")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Metric(Base):
    """Snapshot of a post's performance; earlier snapshots are never overwritten."""

    __tablename__ = "metrics"

    metric_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("met"))
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.post_id", ondelete="CASCADE"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_visits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qualified_inquiries: Mapped[int] = mapped_column(Integer, default=0)
    bookings: Mapped[int] = mapped_column(Integer, default=0)


class ResearchRun(Base):
    """One execution of an automatic collector (success or failure is always recorded)."""

    __tablename__ = "research_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("run"))
    collector: Mapped[str] = mapped_column(String)  # e.g. stackexchange
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")  # running / ok / partial / failed
    requests_made: Mapped[int] = mapped_column(Integer, default=0)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    sources_added: Mapped[int] = mapped_column(Integer, default=0)
    quota_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log: Mapped[str | None] = mapped_column(Text, nullable=True)


class TopicScore(Base):
    """A saved ranking of a topic. Rankings change over time, so they're kept apart from topics."""

    __tablename__ = "topic_scores"

    score_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("score"))
    batch_id: Mapped[str] = mapped_column(String)  # the question batch this ranking was used for
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.topic_id", ondelete="CASCADE"))
    course_relevance: Mapped[float] = mapped_column(Float)
    observed_difficulty: Mapped[float] = mapped_column(Float)
    timeliness: Mapped[float] = mapped_column(Float)
    content_quality_potential: Mapped[float] = mapped_column(Float)
    novelty: Mapped[float] = mapped_column(Float)
    final_score: Mapped[float] = mapped_column(Float)
    evidence_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    score_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    """Key/value store for preferences changed in the Settings page (never the API key)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


CORE_TABLES = ["courses", "course_offerings", "topics", "sources", "questions", "solutions", "posts",
               "metrics", "research_runs"]
