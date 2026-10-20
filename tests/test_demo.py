"""Demo mode: a separate database with sample content for reviewers."""
import pytest

from core import config
from db.repository import Repository
from db.seed import seed_initial_data
from services.demo import load_demo


@pytest.fixture
def repo(tmp_path):
    r = Repository(tmp_path / "demo.db")
    seed_initial_data(r)
    return r


def test_demo_database_path(monkeypatch):
    monkeypatch.delenv("MTS_DB_PATH", raising=False)
    monkeypatch.setenv("MTS_DEMO", "1")
    assert config.is_demo() and config.database_path().name == "demo.db"
    monkeypatch.setenv("MTS_DEMO", "0")
    assert not config.is_demo() and config.database_path().name == "tutoring.db"


def test_demo_content_fills_the_pipeline(repo):
    assert load_demo(repo)
    questions = repo.questions_df()
    assert sorted(questions["selection_status"]) == ["candidate", "favorite", "selected"]
    solutions = repo.solutions_df()
    assert len(solutions) == 1 and bool(solutions.iloc[0]["approved"])
    posts = repo.posts_df()
    assert sorted(posts["platform"]) == ["instagram", "xiaohongshu"]
    assert all(flags == [] for flags in posts["flags"])  # demo drafts pass the app's own checks
    assert all(len(slides) == 5 for slides in posts["slides"])
    sources = repo.sources_df()
    assert len(sources) == 2 and all("Fictional" in n for n in sources["notes"])
    assert not load_demo(repo)  # second call does nothing
    assert len(repo.questions_df()) == 3


def test_demo_mode_pages_render(tmp_path, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("MTS_DB_PATH", str(tmp_path / "ui_demo.db"))
    monkeypatch.setenv("MTS_DEMO", "1")
    st.cache_resource.clear()

    def render(page):
        import importlib
        importlib.import_module(f"ui.{page}").render()

    try:
        for page in ("dashboard", "questions", "workspace", "publisher", "library"):
            at = AppTest.from_function(render, args=(page,), default_timeout=30).run()
            assert not at.exception, (page, [e.value for e in at.exception])
        assert len(Repository(tmp_path / "ui_demo.db").posts_df()) == 2
    finally:
        st.cache_resource.clear()
