import json
import struct

import pytest

from db.repository import Repository, ValidationError
from db.seed import seed_initial_data
from services.slides import build_html, images_outdated, render_input, render_slides

THEME = {"handle": "@me", "background": "#FFFFFF", "text": "#000000", "accent": "#123456"}


@pytest.fixture
def repo(tmp_path):
    r = Repository(tmp_path / "t.db")
    seed_initial_data(r)
    return r


@pytest.fixture
def qid(repo):
    tid = repo.topics_df()["topic_id"].iloc[0]
    [q] = repo.add_questions([dict(topic_id=tid, title="T", question_latex="Prove $x^2\\ge 0$ for real $x$.",
                                   question_type="proof", difficulty=1,
                                   proposed_solution="Cases: $x\\ge0$ or $x<0$.",
                                   teaching_insight="Split into cases.")],
                             batch_id="b", generation_model="m", prompt_version="v")
    return q


# ------------------------------------------------------------------ slides
def test_build_html_escapes_text_and_keeps_maths():
    page = build_html([{"heading": "Why $a<b$?", "body": "Let <script> be **bold**\n\n$$\\frac{1}{2}$$"},
                       {"heading": "Two", "body": "x"}], width=1080, height=1440, label="MAT137 · Limits",
                      theme=THEME)
    assert "Why $a&lt;b$?" in page and "&lt;script&gt;" in page and "<strong>bold</strong>" in page
    assert "$$\\frac{1}{2}$$" in page and "1 / 2" in page and "2 / 2" in page
    assert "@me" in page and "#123456" in page and "width: 1080px; height: 1440px" in page
    assert 'class="slide cover"' in page  # first slide styled as cover


def test_render_real_slides_with_edge(tmp_path):
    slides = [{"heading": "不用计算器，怎么估计 $\\sqrt{10}$？", "body": "$$\\frac{h}{6}$$ 一道期中题。"},
              {"heading": "Long", "body": "word " * 900}]
    result = render_slides(slides, tmp_path / "out", platform="xiaohongshu", label="MAT137", theme=THEME)
    if result.error and ("Executable doesn't exist" in result.error or "msedge" in result.error.lower()):
        pytest.skip("Microsoft Edge not available on this machine")
    assert result.error is None, result.error
    assert [p.name for p in result.paths] == ["slide_1.png", "slide_2.png"]
    width, height = struct.unpack(">II", result.paths[0].read_bytes()[16:24])
    assert (width, height) == (1080, 1440)
    assert result.overflow == [2]  # 900 words can't fit even at the smallest size
    assert json.loads((tmp_path / "out" / "slides.json").read_text(encoding="utf-8")) == slides


def test_images_outdated(repo, tmp_path, monkeypatch):
    import services.slides as slides_mod
    monkeypatch.setattr(slides_mod, "SLIDES_ROOT", tmp_path)
    post = {"post_id": "p1", "slides": [{"heading": "A", "body": "b"}], "use_question_card": False}
    assert not images_outdated(repo, post)  # nothing rendered yet
    (tmp_path / "p1").mkdir()
    (tmp_path / "p1" / "slides.json").write_text(json.dumps(post["slides"]), encoding="utf-8")
    assert not images_outdated(repo, post)
    assert images_outdated(repo, {**post, "slides": [{"heading": "A", "body": "changed"}]})


def test_question_card_replaces_slide_two(repo, qid):
    sid, _ = repo.save_solution(qid, raw_reasoning="Proof.")
    slides = [{"heading": h, "body": "x"} for h in ("Hook", "Problem", "Insight", "Solution", "Close")]
    [pid] = repo.add_posts(sid, [{"platform": "instagram", "language": "en", "slides": slides, "caption": "c"}],
                           draft_round="r", cta_style="soft")
    post = repo.posts_df().set_index("post_id").loc[pid].to_dict() | {"post_id": pid}
    assert post["use_question_card"]  # on by default for English posts
    rendered = render_input(repo, post)
    assert len(rendered) == 5 and rendered[1]["kind"] == "question"
    assert rendered[1]["body"] == "Prove $x^2\\ge 0$ for real $x$." and rendered[1]["difficulty"] == 1
    assert render_input(repo, {**post, "use_question_card": False})[1]["heading"] == "Problem"
    page = build_html(rendered, width=1080, height=1350, label="L", theme=THEME)
    assert 'class="slide question"' in page and "PROBLEM" in page and "●○○○○" in page


# ------------------------------------------------------------- verify mode
def test_start_from_ai_solution(repo, qid):
    sid = repo.start_from_ai(qid)
    sol = repo.get_solution(qid)
    assert sol["origin"] == "ai_verified" and sol["raw_reasoning"].startswith("Cases")
    assert sol["intuition"] == "Split into cases." and not sol["approved"]
    assert list(repo.revisions_df(sid)["author"]) == ["ai"]

    repo.save_solution(qid, raw_reasoning="Cases: if $x\\ge0$, $x^2=x\\cdot x\\ge0$; else $x^2=(-x)^2\\ge0$.")
    assert list(repo.revisions_df(sid)["author"]) == ["you", "ai"]  # newest first
    assert repo.get_solution(qid)["origin"] == "ai_verified"  # origin is kept for your records

    with pytest.raises(ValidationError):
        repo.start_from_ai(qid)  # already has a solution


def test_own_solution_origin(repo, qid):
    repo.save_solution(qid, raw_reasoning="My own proof.")
    assert repo.get_solution(qid)["origin"] == "own"
