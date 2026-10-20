"""Headless UI tests: run each Streamlit page against a temporary database."""
import json

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from db.repository import Repository


def _render(page: str) -> None:
    import importlib

    importlib.import_module(f"ui.{page}").render()


def run_page(page: str) -> AppTest:
    at = AppTest.from_function(_render, args=(page,), default_timeout=30)
    return at.run()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    monkeypatch.setenv("MTS_DB_PATH", str(path))
    st.cache_resource.clear()  # get_repo() is cached per process
    yield path
    st.cache_resource.clear()


def click(at: AppTest, label: str) -> AppTest:
    """Click the (last) button with this label and rerun."""
    return [b for b in at.button if b.label == label][-1].click().run()


def no_errors(at: AppTest) -> None:
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("page", ["dashboard", "research", "questions", "workspace", "publisher", "library",
                                  "courses", "topics", "settings"])
def test_every_page_renders(db, page):
    no_errors(run_page(page))


def test_workspace_to_publishing_flow(db):
    run_page("dashboard")  # creates and seeds the database
    repo = Repository(db)
    tid = repo.topics_df()["topic_id"].iloc[0]
    [qid] = repo.add_questions([dict(topic_id=tid, title="Test question",
                                     question_latex="Show that $x^2 \\ge 0$ for all real $x$.",
                                     question_type="proof", difficulty=1)],
                               batch_id="b", generation_model="m", prompt_version="v")
    repo.set_question_status(qid, selection_status="selected")

    # 1. write and save a solution
    at = run_page("workspace")
    no_errors(at)
    at.text_area(key=f"ws_raw_reasoning_{qid}").input("If $x\\ge0$ then $x^2=x\\cdot x\\ge0$; otherwise use $(-x)^2$.")
    click(at, "Save solution")
    no_errors(at)
    sol = repo.get_solution(qid)
    assert sol and "x^2" in sol["raw_reasoning"]

    # 2. build the review prompt, paste a review back
    at.button(key="review_build").click().run()
    no_errors(at)
    assert any("Task: MATH_REVIEW" in c.value for c in at.code)
    review = {"task": "MATH_REVIEW", "solution_id": sol["solution_id"], "verdict": "minor_issues",
              "summary": "Correct.", "issues": [{"severity": "style", "description": "Say 'real'."}]}
    at.text_area[-1].input(json.dumps(review))
    click(at, "Import reply")
    no_errors(at)
    assert repo.get_question(qid)["review_status"] == "ai_checked"

    # 3. approve
    at.checkbox(key=f"approve_{sol['solution_id']}").check().run()
    no_errors(at)
    assert repo.get_solution(qid)["approved"]

    # 4. publishing: build drafts prompt and import drafts
    at = run_page("publisher")
    no_errors(at)
    at.button(key="drafts_build").click().run()
    assert any("Task: DRAFTS" in c.value for c in at.code)
    slides = [{"heading": h, "body": "$x^2\\ge0$"} for h in ("Hook", "Problem", "Insight", "Solution", "Book")]
    drafts = {"task": "DRAFTS", "solution_id": sol["solution_id"], "drafts": [
        {"platform": "xiaohongshu", "language": "zh", "title_options": ["平方为什么非负", "一个小证明", "期中证明题"],
         "caption": "很多同学会直接说平方是非负的，但证明要分情况讨论。" * 4, "hashtags": ["微积分"],
         "cta": "欢迎私信。", "slides": slides},
        {"platform": "instagram", "language": "en", "title_options": ["Squares are non-negative", "Why?", "Exam"],
         "caption": "Why is x² ≥ 0? A short proof by cases. DM me to book.", "hashtags": ["math"],
         "cta": "DM me.", "slides": slides}]}
    at.text_area[-1].input(json.dumps(drafts, ensure_ascii=False))
    click(at, "Import reply")
    no_errors(at)
    posts = repo.posts_df()
    assert sorted(posts["platform"]) == ["instagram", "xiaohongshu"]
    assert any("平方为什么非负" in c.value for c in at.code)  # copy-ready text shown


def test_verify_mode_from_ai_solution(db):
    run_page("dashboard")
    repo = Repository(db)
    tid = repo.topics_df()["topic_id"].iloc[0]
    [qid] = repo.add_questions([dict(topic_id=tid, title="Verify me", question_latex=r"Show $1+1=2$ in $\mathbb N$.",
                                     question_type="proof", difficulty=1, proposed_solution="By definition of $+$.")],
                               batch_id="b", generation_model="m", prompt_version="v")
    repo.set_question_status(qid, selection_status="selected")
    at = run_page("workspace")
    at.button(key=f"from_ai_{qid}").click().run()
    no_errors(at)
    sol = repo.get_solution(qid)
    assert sol["origin"] == "ai_verified" and at.text_area(key=f"ws_raw_reasoning_{qid}").value == "By definition of $+$."
    at.checkbox(key=f"approve_{sol['solution_id']}").check().run()
    assert repo.get_solution(qid)["approved"]


def test_library_with_questions(db):
    run_page("dashboard")
    repo = Repository(db)
    topics = repo.topics_df()["topic_id"]
    repo.add_questions([dict(topic_id=t, title=f"Q{i}", question_latex="Some question text.", question_type="proof",
                             difficulty=1 + i % 5) for i, t in enumerate(list(topics) * 2)],
                       batch_id="b", generation_model="m", prompt_version="v")
    at = run_page("library")
    no_errors(at)
    assert at.metric[0].value == str(2 * len(topics))
    assert len(at.get("vega_lite_chart")) == 2  # topic chart and difficulty chart


def test_topic_suggestions_by_pasting(db):
    run_page("dashboard")
    repo = Repository(db)
    at = run_page("research")
    at.radio(key="sg_how").set_value("Paste text").run()
    at.selectbox(key="sg_type").set_value("textbook_contents").run()
    at.text_input(key="sg_title").input("Stewart 9e contents").run()
    at.text_area(key="sg_text").input("11.10 Taylor and Maclaurin Series. " * 30).run()
    click(at, "Save text")
    no_errors(at)
    assert list(repo.course_documents_df("uoft_mat137")["title"]) == ["Stewart 9e contents"]
    click(at, "Build topic prompt")
    assert any(c.value.startswith("Task: TOPICS") for c in at.code)
    reply = {"task": "TOPICS", "course_id": "uoft_mat137", "notes": "", "topics": [
        {"name": "Taylor remainder estimates", "subject": "Calculus", "target_level": "intro_proofs",
         "difficulty_level": 3, "search_query": "taylor remainder", "evidence_type": "textbook_contents",
         "syllabus_evidence": "Contents: 11.10"}]}
    at.text_area(key="topics_reply").input(json.dumps(reply))
    click(at, "Import reply")
    no_errors(at)
    click(at, "Add ticked topics")
    no_errors(at)
    assert "Taylor remainder estimates" in set(repo.topics_df()["name"])


def test_sections_fold(db):
    at = run_page("questions")
    assert any(b.label.endswith("2 · Generate questions**") for b in at.button)
    click(at, "▾ **2 · Generate questions**")
    no_errors(at)
    assert any(b.label == "▸ **2 · Generate questions**" for b in at.button)
    assert not any(b.label == "Build prompt" for b in at.button)  # folded body isn't rendered


def test_topic_prompt_with_attachment_only(db):
    run_page("dashboard")
    repo = Repository(db)
    at = run_page("research")
    at.checkbox(key="sg_att").check().run()
    at.selectbox(key="sg_att_type").set_value("course_notes").run()
    at.text_input(key="sg_att_title").input("MAT137 notes pages 3-5").run()
    click(at, "Build topic prompt")
    no_errors(at)
    assert any("attached to this chat message" in c.value for c in at.code)
    assert any(t.startswith("Attached in ChatGPT:") for t in repo.sources_df()["title"])


def test_browse_source_without_excerpt(db):
    """Regression: a source with no excerpt (e.g. an 'Attached in ChatGPT' record) crashed Browse sources."""
    run_page("dashboard")
    repo = Repository(db)
    repo.add_source(source_type="stackexchange", title="With excerpt", url="https://x.test/1", content_excerpt="text")
    from services.topic_suggester import record_attachment
    record_attachment(repo, course_id="uoft_mat137", offering_id=None, doc_type="course_notes", title="Scan p.3")
    at = run_page("research")
    no_errors(at)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "deepseek", "compatible"])
def test_settings_in_api_mode(db, provider):
    run_page("dashboard")
    repo = Repository(db)
    repo.set_setting("ai_mode", "api")
    repo.set_setting("api_provider", provider)
    no_errors(run_page("settings"))
    no_errors(run_page("questions"))
