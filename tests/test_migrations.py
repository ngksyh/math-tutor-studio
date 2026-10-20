"""Upgrading a database created by the original MVP 1 schema."""
import sqlite3

from sqlalchemy import inspect

from db.migrations import LATEST_VERSION
from db.repository import Repository

V1_SCHEMA = """
CREATE TABLE courses (
    course_id VARCHAR NOT NULL, university VARCHAR NOT NULL, course_code VARCHAR NOT NULL,
    course_title VARCHAR NOT NULL, level INTEGER NOT NULL, priority INTEGER NOT NULL,
    active BOOLEAN NOT NULL, syllabus_url VARCHAR, exam_notes TEXT, last_verified_at DATETIME,
    created_at DATETIME NOT NULL, PRIMARY KEY (course_id)
);
CREATE TABLE settings ("key" VARCHAR NOT NULL, value TEXT, updated_at DATETIME NOT NULL,
    PRIMARY KEY ("key"));
CREATE TABLE sources (
    source_id VARCHAR NOT NULL, source_type VARCHAR NOT NULL, title VARCHAR NOT NULL, url VARCHAR,
    platform_id VARCHAR, published_at DATETIME, fetched_at DATETIME NOT NULL, course_id VARCHAR,
    content_excerpt TEXT, engagement_count INTEGER, license VARCHAR, content_hash VARCHAR NOT NULL,
    collection_method VARCHAR NOT NULL, collection_status VARCHAR NOT NULL, excluded BOOLEAN NOT NULL,
    notes TEXT, PRIMARY KEY (source_id),
    FOREIGN KEY(course_id) REFERENCES courses (course_id) ON DELETE SET NULL, UNIQUE (content_hash)
);
INSERT INTO courses VALUES ('uoft_mat137','UofT','MAT137','Calculus with Proofs',100,5,1,
    'https://example.edu/mat137',NULL,NULL,'2026-10-11 00:00:00');
INSERT INTO sources VALUES ('src_old','university_course_page','MAT464 from UofT','https://x.test/464',
    NULL,NULL,'2026-10-11 00:00:00','uoft_mat137',NULL,NULL,NULL,'hash1','manual','ok',0,NULL);
"""


def test_upgrade_v1_database_keeps_data(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(V1_SCHEMA)

    repo = Repository(path)

    cols = {c["name"] for c in inspect(repo.engine).get_columns("courses")}
    assert "calendar_url" in cols and not {"syllabus_url", "exam_notes", "last_verified_at"} & cols
    assert "offering_id" in {c["name"] for c in inspect(repo.engine).get_columns("sources")}
    assert repo.get_setting("schema_version") == str(LATEST_VERSION)

    course = repo.courses_df().iloc[0]
    assert course["calendar_url"] == "https://example.edu/mat137"
    source = repo.sources_df().iloc[0]
    assert source["title"] == "MAT464 from UofT" and source["course_id"] == "uoft_mat137"

    # the upgraded database supports the new features
    oid = repo.add_offering("uoft_mat137", 2011, "Fall")
    repo.set_source_scope("src_old", None, oid)
    assert repo.sources_df().iloc[0]["term"] == "2011 Fall"

    # opening again is a no-op
    Repository(path)
    assert repo.get_setting("schema_version") == str(LATEST_VERSION)


def test_new_database_starts_at_latest_version(tmp_path):
    repo = Repository(tmp_path / "new.db")
    assert repo.get_setting("schema_version") == str(LATEST_VERSION)
