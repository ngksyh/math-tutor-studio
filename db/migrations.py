"""Schema upgrades for existing databases.

A fresh database is created directly at the latest schema. An existing one is upgraded step by
step; the version is stored in the settings table under 'schema_version'. To change the schema
later: edit models.py, append a migration function below, and bump nothing else.
"""
from __future__ import annotations

from typing import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from db.models import Base


def _columns(conn: Connection, table: str) -> set[str]:
    return {c["name"] for c in inspect(conn).get_columns(table)}


def _add_column(conn: Connection, table: str, column: str, sql_type: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def _drop_column(conn: Connection, table: str, column: str) -> None:
    if column in _columns(conn, table):
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))


def _v2_course_offerings(conn: Connection) -> None:
    """Move term-specific fields from courses to course_offerings; link sources to offerings."""
    if "syllabus_url" in _columns(conn, "courses"):
        conn.execute(text("ALTER TABLE courses RENAME COLUMN syllabus_url TO calendar_url"))
    for col in ("exam_notes", "last_verified_at"):
        if col in _columns(conn, "courses"):
            conn.execute(text(f"ALTER TABLE courses DROP COLUMN {col}"))
    if "offering_id" not in _columns(conn, "sources"):
        conn.execute(text(
            "ALTER TABLE sources ADD COLUMN offering_id VARCHAR "
            "REFERENCES course_offerings(offering_id) ON DELETE SET NULL"
        ))


def _v3_research_and_questions(conn: Connection) -> None:
    """Per-topic search query and question batches (new tables are made by create_all)."""
    if "search_query" not in _columns(conn, "topics"):
        conn.execute(text("ALTER TABLE topics ADD COLUMN search_query VARCHAR"))
    if "batch_id" not in _columns(conn, "questions"):
        conn.execute(text("ALTER TABLE questions ADD COLUMN batch_id VARCHAR"))


def _v4_publishing(conn: Connection) -> None:
    """Post title options, slides, warning flags and draft rounds (solution_revisions is new)."""
    cols = _columns(conn, "posts")
    for name, sql_type in (("title_options", "JSON"), ("slides", "JSON"), ("flags", "JSON"),
                           ("draft_round", "VARCHAR"), ("cta_style", "VARCHAR")):
        if name not in cols:
            conn.execute(text(f"ALTER TABLE posts ADD COLUMN {name} {sql_type}"))


def _v5_verify_mode(conn: Connection) -> None:
    """Record whether a solution started from the AI draft, and who wrote each revision."""
    if "origin" not in _columns(conn, "solutions"):
        conn.execute(text("ALTER TABLE solutions ADD COLUMN origin VARCHAR NOT NULL DEFAULT 'own'"))
    if "author" not in _columns(conn, "solution_revisions"):
        conn.execute(text("ALTER TABLE solution_revisions ADD COLUMN author VARCHAR NOT NULL DEFAULT 'you'"))


# Index i upgrades schema version i+1 -> i+2. Version 1 = the original MVP 1 schema.
MIGRATIONS: list[Callable[[Connection], None]] = [
    _v2_course_offerings,
    _v3_research_and_questions,
    _v4_publishing,
    _v5_verify_mode,
    lambda conn: _add_column(conn, "posts", "use_question_card", "BOOLEAN NOT NULL DEFAULT 0"),  # v6
    lambda conn: _add_column(conn, "sources", "full_text", "TEXT"),  # v7: reusable course documents
    lambda conn: _add_column(conn, "sources", "doc_scope", "VARCHAR"),  # v8: chapters in scope
    lambda conn: _add_column(conn, "sources", "file_path", "VARCHAR"),  # v9: uploaded original file
    lambda conn: _drop_column(conn, "topics", "evidence_status"),  # v10: observed/hypothesized label removed
]
LATEST_VERSION = len(MIGRATIONS) + 1


def _get_version(conn: Connection) -> int:
    row = conn.execute(text("SELECT value FROM settings WHERE key = 'schema_version'")).fetchone()
    return int(row[0]) if row else 1


def _set_version(conn: Connection, version: int) -> None:
    conn.execute(text(
        "INSERT INTO settings (key, value, updated_at) VALUES ('schema_version', :v, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value = :v, updated_at = CURRENT_TIMESTAMP"
    ), {"v": str(version)})


def prepare_schema(engine: Engine) -> None:
    """Create a new database at the latest schema, or upgrade an existing one."""
    is_new = not inspect(engine).has_table("courses")
    Base.metadata.create_all(engine)  # creates any missing tables; never alters existing ones
    with engine.begin() as conn:
        if is_new:
            _set_version(conn, LATEST_VERSION)
            return
        version = _get_version(conn)
        for step in range(version - 1, len(MIGRATIONS)):
            MIGRATIONS[step](conn)
            _set_version(conn, step + 2)
