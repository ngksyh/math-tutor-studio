import json

import pytest

from db.repository import Repository, ValidationError
from db.seed import seed_initial_data
from services.draft_generator import build_drafts_prompt, check_post, copy_text, parse_drafts
from services.exporter import export_package
from services.math_reviewer import build_review_prompt, load_review, parse_review, save_review
from services.prompting import ReplyFormatError


@pytest.fixture
def repo(tmp_path):
    r = Repository(tmp_path / "t.db")
    seed_initial_data(r)
    return r


@pytest.fixture
def qid(repo):
    tid = repo.topics_df().set_index("name").loc["Mean Value Theorem", "topic_id"]
    [q] = repo.add_questions([dict(topic_id=tid, title="Bounding sqrt(10)",
                                   question_latex=r"Use the MVT to show $\sqrt{10}<\frac{19}{6}$.",
                                   question_type="proof", difficulty=3)],
                             batch_id="b", generation_model="m", prompt_version="v")
    repo.set_question_status(q, selection_status="selected")
    return q


REASONING = r"Apply the MVT to $\sqrt{x}$ on $[9,10]$: $\sqrt{10}-3=\frac{1}{2\sqrt c}<\frac16$."


# ---------------------------------------------------------------- solutions
def test_solution_revisions_and_approval_reset(repo, qid):
    sid, changed = repo.save_solution(qid, raw_reasoning=REASONING)
    assert changed and len(repo.revisions_df(sid)) == 1
    assert repo.save_solution(qid, raw_reasoning=REASONING) == (sid, False)  # no change, no revision
    assert len(repo.revisions_df(sid)) == 1

    repo.set_solution_approved(sid, True)
    assert repo.get_question(qid)["review_status"] == "verified"
    repo.save_solution(qid, raw_reasoning=REASONING + " Hence the bound.", intuition="Bound the derivative.")
    sol = repo.get_solution(qid)
    assert not sol["approved"] and repo.get_question(qid)["review_status"] == "unreviewed"
    revs = repo.revisions_df(sid)
    assert len(revs) == 2 and revs.iloc[1]["raw_reasoning"] == REASONING  # original kept


def test_empty_reasoning_rejected(repo, qid):
    with pytest.raises(ValidationError):
        repo.save_solution(qid, raw_reasoning="   ")


# -------------------------------------------------------------- math review
def review_reply(**kw):
    data = {"task": "MATH_REVIEW", "solution_id": "x", "verdict": "minor_issues", "summary": "Fine overall.",
            "issues": [{"severity": "minor", "location": "on [9,10]", "description": "State continuity on \\(I\\).",
                        "suggestion": "Add one line."}]}
    data.update(kw)
    return "```json\n" + json.dumps(data) + "\n```"


def test_review_prompt_and_import(repo, qid):
    sid, _ = repo.save_solution(qid, raw_reasoning=REASONING, common_mistakes="Forgetting continuity.")
    prompt = build_review_prompt(repo, qid, include_rules=False)
    assert prompt.startswith("Task: MATH_REVIEW") and sid in prompt and "Forgetting continuity." in prompt

    review = parse_review(review_reply(solution_id=sid), sid)
    assert review.result.verdict == "minor_issues" and not review.warnings
    assert review.result.issues[0].description == "State continuity on $I$."  # delimiters normalised
    save_review(repo, sid, review, "chatgpt-manual")
    assert repo.get_question(qid)["review_status"] == "ai_checked"
    assert load_review(repo.get_solution(qid)["math_review_notes"])["verdict"] == "minor_issues"


def test_major_issue_forces_verdict_and_flags(repo, qid):
    sid, _ = repo.save_solution(qid, raw_reasoning=REASONING)
    reply = review_reply(solution_id=sid, verdict="no_issues_found",
                         issues=[{"severity": "major", "description": "Wrong inequality direction."}])
    review = parse_review(reply, sid)
    assert review.result.verdict == "major_issues" and review.warnings
    save_review(repo, sid, review, "m")
    assert repo.get_question(qid)["review_status"] == "flagged"


def test_review_rejects_wrong_task_and_bad_format(repo, qid):
    with pytest.raises(ReplyFormatError):
        parse_review(review_reply(task="DRAFTS"), "x")
    with pytest.raises(ReplyFormatError):
        parse_review(review_reply(verdict="looks good"), "x")


# ------------------------------------------------------------------- drafts
def drafts_reply(sid, **overrides):
    xhs = {"platform": "xiaohongshu", "language": "zh",
           "title_options": ["MAT137 中值定理估算√10", "不用计算器怎么估√10？", "期中必考：MVT不等式"],
           "caption": "很多同学学中值定理时只会背公式。这道题用 √x 在 [9,10] 上的中值定理，" * 3 + "欢迎私信预约一对一辅导。",
           "hashtags": ["#MAT137", "微积分", "微积分"], "keywords": ["中值定理"], "cta": "欢迎私信预约。",
           "slides": [{"heading": h, "body": "Let $f(x)=\\sqrt{x}$."} for h in ("Hook", "Problem", "Insight", "Solution", "Book")]}
    ig = {"platform": "instagram", "language": "en",
          "title_options": ["MAT137: MVT bounds", "Estimate √10 with no calculator", "Midterm MVT trick"],
          "caption": "Can you bound √10 without a calculator? " * 5 + "DM me to book a session.",
          "hashtags": ["calculus", "uoft"], "cta": "DM me to book a session.",
          "slides": [{"heading": h, "body": "Let $f(x)=\\sqrt{x}$."} for h in ("Hook", "Problem", "Insight", "Solution", "Book")]}
    data = {"task": "DRAFTS", "solution_id": sid, "drafts": [xhs, ig], "concerns": []}
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_drafts_require_approval_and_include_context(repo, qid):
    sid, _ = repo.save_solution(qid, raw_reasoning=REASONING, intuition="Bound the derivative.")
    with pytest.raises(ValueError):
        build_drafts_prompt(repo, sid, platforms=["instagram"], cta_style="soft", include_rules=False)
    repo.set_solution_approved(sid, True)
    prompt = build_drafts_prompt(repo, sid, platforms=["xiaohongshu", "instagram"], cta_style="direct",
                                 include_rules=True)
    assert prompt.startswith("Task: DRAFTS") and "CTA style: direct" in prompt
    assert "(Your degree, institution and distinctions)" in prompt  # profile placeholder from config
    assert "Bound the derivative." in prompt and "UofT MAT137" in prompt
    assert "加拿大留学" in prompt  # suggested hashtags
    assert "Task: DRAFTS — turn" in prompt and "Task: QUESTIONS" not in prompt  # only this task's rules


def test_parse_drafts_cleans_and_flags(repo, qid):
    result = parse_drafts(drafts_reply("s1"), expected_solution_id="s1", platforms=["xiaohongshu", "instagram"])
    assert not result.rejected and not result.warnings
    xhs, ig = result.drafts
    assert xhs["hashtags"] == ["MAT137", "微积分"]  # '#' stripped, duplicate removed
    assert xhs["title"] == "MAT137 中值定理估算√10"
    assert xhs["flags"] == [] and ig["flags"] == []  # within limits, right language, no claims


def test_parse_drafts_rejects_unrequested_and_duplicate(repo, qid):
    data = json.loads(drafts_reply("s1"))
    data["drafts"].append(dict(data["drafts"][1]))
    result = parse_drafts(json.dumps(data, ensure_ascii=False), expected_solution_id="s1", platforms=["instagram"])
    assert len(result.drafts) == 1 and len(result.rejected) == 2  # xhs not requested + 2nd instagram


def test_check_post_flags():
    flags = check_post("xiaohongshu", title="这是一个非常非常非常非常非常非常长的小红书标题啊", title_options=[],
                       caption="We guarantee an A+ for $40/hr: $\\frac{1}{2}$", hashtags=[], cta="", slides=[])
    text = " ".join(flags)
    assert "Title is" in text and "doesn't look like Chinese" in text and "LaTeX" in text
    assert "promise" in text and "price" in text and "0 slides" in text


def test_posts_edit_export_and_copy(repo, qid, tmp_path):
    sid, _ = repo.save_solution(qid, raw_reasoning=REASONING)
    repo.set_solution_approved(sid, True)
    drafts = parse_drafts(drafts_reply(sid), expected_solution_id=sid, platforms=["xiaohongshu", "instagram"]).drafts
    ids = repo.add_posts(sid, drafts, draft_round="r1", cta_style="soft")
    repo.update_post(ids[1], title="Custom title", status="published", published_url="https://instagram.com/p/x")
    post = repo.posts_df(sid).set_index("post_id").loc[ids[1]]
    assert post["published_at"] is not None and post["status"] == "published"
    assert copy_text(post.to_dict()).startswith("Custom title") and "#calculus #uoft" in copy_text(post.to_dict())
    with pytest.raises(ValidationError):
        repo.update_post(ids[0], status="viral")

    folder, zip_bytes = export_package(repo, sid, root=tmp_path / "exports")
    assert {p.name for p in folder.iterdir()} == {"solution.md", "xiaohongshu.md", "instagram.md"}
    assert "√10" in (folder / "xiaohongshu.md").read_text(encoding="utf-8") and zip_bytes[:2] == b"PK"
    assert repo.solutions_df().set_index("solution_id").loc[sid, "post_count"] == 2


def test_soft_posts_teach_only(repo, qid):
    sid, _ = repo.save_solution(qid, raw_reasoning=REASONING)
    repo.set_solution_approved(sid, True)
    soft = build_drafts_prompt(repo, sid, platforms=["instagram"], cta_style="soft", include_rules=False)
    direct = build_drafts_prompt(repo, sid, platforms=["instagram"], cta_style="direct", include_rules=False)
    assert "Tutor profile" not in soft and "Tutor profile" in direct

    # the sample drafts advertise tutoring ("私信预约一对一辅导", "DM me to book") -> flagged when soft
    result = parse_drafts(drafts_reply(sid), expected_solution_id=sid, platforms=["xiaohongshu", "instagram"],
                          cta_style="soft")
    assert all(any("teaching-only" in f for f in d["flags"]) for d in result.drafts)
    direct_result = parse_drafts(drafts_reply(sid), expected_solution_id=sid,
                                 platforms=["xiaohongshu", "instagram"], cta_style="direct")
    assert not any("teaching-only" in f for d in direct_result.drafts for f in d["flags"])
