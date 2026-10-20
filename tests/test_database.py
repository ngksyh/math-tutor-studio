import pytest

from db.repository import Repository, ValidationError, content_hash, make_course_id
from db.seed import INITIAL_COURSES, STARTER_TOPICS, seed_initial_data


@pytest.fixture
def repo(tmp_path):
    r = Repository(tmp_path / "test.db")
    seed_initial_data(r)
    return r


def test_seed_is_idempotent(repo):
    assert seed_initial_data(repo) == {"courses": 0, "topics": 0}
    assert len(repo.courses_df()) == len(INITIAL_COURSES)
    assert len(repo.topics_df()) == len(STARTER_TOPICS)


def test_offerings_per_term(repo):
    old = repo.add_offering("uoft_mat137", 2011, "Fall", course_url="https://x.test/2011")
    new = repo.add_offering("uoft_mat137", 2026, "Fall", section="LEC0101", monitor=True)
    repo.add_offering("uoft_mat137", 2026, "Winter")
    with pytest.raises(ValidationError):
        repo.add_offering("uoft_mat137", 2011, "Fall")  # same term + section
    with pytest.raises(ValidationError):
        repo.add_offering("uoft_mat137", 1800, "Fall")

    df = repo.offerings_df("uoft_mat137")
    assert list(df["term"]) == ["2026 Fall · LEC0101", "2026 Winter", "2011 Fall"]  # newest first
    assert repo.courses_df().set_index("course_id").loc["uoft_mat137", "offerings"] == 3

    choices = repo.course_scope_choices()
    assert choices[("uoft_mat137", None)] == "UofT MAT137 (any term)"
    assert choices[("uoft_mat137", new)] == "UofT MAT137 — 2026 Fall · LEC0101"

    df.loc[df["offering_id"] == old, "instructor"] = "Someone"
    assert repo.update_offerings(df) == 1


def test_source_on_offering_sets_course(repo):
    oid = repo.add_offering("uoft_mat223", 2011, "Fall")
    sid, _ = repo.add_source(source_type="university_course_page", title="Old page", offering_id=oid)
    row = repo.sources_df().set_index("source_id").loc[sid]
    assert row["course_id"] == "uoft_mat223" and row["term"] == "2011 Fall"
    # deleting the term keeps the source but unlinks the term
    repo.delete_offering(oid)
    row = repo.sources_df().set_index("source_id").loc[sid]
    assert row["offering_id"] is None and row["course_id"] == "uoft_mat223"


def test_delete_course_keeps_sources_and_topics(repo):
    sid, _ = repo.add_source(source_type="other", title="S", course_id="ubc_math221")
    n_topics = len(repo.topics_df())
    repo.delete_course("ubc_math221")
    assert repo.sources_df().set_index("source_id").loc[sid, "course_id"] is None
    assert len(repo.topics_df()) == n_topics


def test_custom_subject_is_accepted_and_offered(repo):
    repo.add_topic("Gauss–Bonnet theorem", "Differential Geometry")
    repo.add_topic("Morse lemma", "Morse Theory")
    subjects = repo.subjects()
    assert "Differential Geometry" in subjects and subjects[-1] == "Morse Theory"
    with pytest.raises(ValidationError):
        repo.add_topic("No subject", "  ")


def test_add_course_and_duplicate(repo):
    cid = repo.add_course("SFU", "MATH 151", "Calculus I", 100, 3)
    assert cid == "sfu_math_151"
    with pytest.raises(ValidationError):
        repo.add_course("SFU", "MATH 151", "Calculus I")


def test_update_courses_from_editor(repo):
    df = repo.courses_df()
    df.loc[df["course_id"] == "uoft_mat137", "priority"] = 2
    df.loc[df["course_id"] == "uoft_mat137", "active"] = False
    assert repo.update_courses(df) == 2
    row = repo.courses_df().set_index("course_id").loc["uoft_mat137"]
    assert row["priority"] == 2 and not row["active"]


def test_update_courses_rejects_bad_priority(repo):
    df = repo.courses_df()
    df.loc[0, "priority"] = 9
    with pytest.raises(ValidationError):
        repo.update_courses(df)


def test_topic_with_courses(repo):
    tid = repo.add_topic("Uniform continuity", "Real Analysis", ["uoft_mat137"],
                         target_level="intro_proofs")
    row = repo.topics_df().set_index("topic_id").loc[tid]
    assert row["course_ids"] == ["uoft_mat137"]
    repo.set_topic_courses(tid, ["uoft_mat137", "ubc_math100"])
    assert repo.topics_df().set_index("topic_id").loc[tid, "course_ids"] == ["ubc_math100", "uoft_mat137"]


def test_topic_validation(repo):
    with pytest.raises(ValidationError):
        repo.add_topic("X", "Calculus", difficulty_level=9)
    with pytest.raises(ValidationError):
        repo.add_topic("  ", "Calculus")


def test_source_dedup_and_topic_links(repo):
    tid = repo.topics_df()["topic_id"].iloc[0]
    sid, created = repo.add_source(source_type="stackexchange", title="Q about limits",
                                   url="https://math.stackexchange.com/q/1/", content_excerpt="Why  delta?",
                                   course_id="uoft_mat137", topic_ids=[tid])
    assert created
    # same URL (different case / trailing slash) and excerpt (extra whitespace) is a duplicate
    sid2, created2 = repo.add_source(source_type="stackexchange", title="Different title",
                                     url="https://MATH.stackexchange.com/q/1", content_excerpt="why delta?")
    assert (sid2, created2) == (sid, False)

    df = repo.sources_df()
    assert len(df) == 1 and df.iloc[0]["topic_ids"] == [tid]
    assert repo.topics_df().set_index("topic_id").loc[tid, "source_count"] == 1


def test_unknown_engagement_stays_null(repo):
    repo.add_source(source_type="other", title="No count")
    assert repo.sources_df()["engagement_count"].isna().all()


def test_exclude_source(repo):
    repo.add_source(source_type="other", title="A")
    df = repo.sources_df()
    df["excluded"] = True
    repo.update_sources(df)
    assert repo.sources_df(include_excluded=False).empty


def test_settings_roundtrip(repo):
    assert repo.get_setting("ai_model", "fallback") == "fallback"
    repo.set_setting("ai_model", "gpt-x")
    repo.set_setting("ai_model", "gpt-y")
    assert repo.get_setting("ai_model") == "gpt-y"


def test_helpers():
    assert make_course_id("UBC", "MATH 100") == "ubc_math_100"
    assert content_hash(None, None, "Title") == content_hash(None, "", " title ")


def test_backup_includes_recent_changes(repo, tmp_path):
    """Regression: copying tutoring.db alone missed changes still in the WAL side file."""
    repo.add_topic("Backup check topic", "Calculus")
    data = repo.backup_bytes()
    restored_path = tmp_path / "restored.db"
    restored_path.write_bytes(data)
    assert "Backup check topic" in set(Repository(restored_path).topics_df()["name"])
    copy = repo.backup_to(tmp_path / "copy" / "c.db")
    assert "Backup check topic" in set(Repository(copy).topics_df()["name"])


def test_fresh_database_is_created_and_seeded(tmp_path):
    path = tmp_path / "new_folder" / "tutoring.db"
    repo = Repository(path)
    seed_initial_data(repo)
    assert path.exists() and len(repo.courses_df()) == 4 and len(repo.topics_df()) == 7
