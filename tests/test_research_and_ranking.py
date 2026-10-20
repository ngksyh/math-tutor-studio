from datetime import datetime

import httpx
import pytest

from collectors.stackexchange import StackExchangeClient, html_to_text
from db.repository import Repository
from db.seed import seed_initial_data
from services.research import run_stackexchange, search_plan
from services.topic_ranker import rank_topics


@pytest.fixture
def repo(tmp_path):
    r = Repository(tmp_path / "t.db")
    seed_initial_data(r)
    return r


def se_item(qid, score, title="Why &quot;MVT&quot;?"):
    return {"question_id": qid, "title": title, "link": f"https://math.stackexchange.com/q/{qid}",
            "score": score, "view_count": 100, "answer_count": 2, "creation_date": 1600000000,
            "tags": ["calculus"], "body": "<p>Let $f$ be <b>differentiable</b>.</p><pre>code</pre>",
            "owner": {"display_name": "alice"}, "content_license": "CC BY-SA 4.0"}


def mock_client(handler):
    return StackExchangeClient(http=httpx.Client(transport=httpx.MockTransport(handler)), request_delay=0)


# ---------------------------------------------------------------- collector
def test_html_to_text():
    assert html_to_text("<p>a &amp; $x^2$</p><pre>junk</pre>", 100) == "a & $x^2$ [code]"
    assert html_to_text("<p>" + "word " * 50 + "</p>", 20).endswith("…")


def test_search_parses_and_sends_tags():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"items": [se_item(1, 10)], "quota_remaining": 250})

    result = mock_client(handler).search("mean value theorem", "calculus")
    assert seen["tagged"] == "calculus" and seen["site"] == "math" and seen["q"] == "mean value theorem"
    post = result.posts[0]
    assert post.title == 'Why "MVT"?' and post.platform_id == "math.se:1" and post.author == "alice"
    assert result.quota_remaining == 250


def test_run_adds_then_refreshes_without_duplicates(repo):
    score = {"value": 10}

    def handler(request):
        items = [se_item(1, score["value"]), se_item(2, 0)]  # second is below min_score
        return httpx.Response(200, json={"items": items, "quota_remaining": 200})

    mvt = repo.topics_df().set_index("name").loc["Mean Value Theorem", "topic_id"]
    first = run_stackexchange(repo, topic_ids=[mvt], client=mock_client(handler))
    assert first.status == "ok" and first.sources_added == 1

    score["value"] = 42
    second = run_stackexchange(repo, topic_ids=[mvt], client=mock_client(handler))
    assert second.sources_added == 0 and second.sources_refreshed == 1
    src = repo.sources_df()
    assert len(src) == 1 and src.iloc[0]["engagement_count"] == 42 and src.iloc[0]["topic_ids"] == [mvt]
    assert src.iloc[0]["license"] == "CC BY-SA 4.0" and src.iloc[0]["collection_method"] == "api"
    assert list(repo.runs_df()["status"]) == ["ok", "ok"]


def test_failed_requests_are_logged(repo):
    def handler(request):
        return httpx.Response(400, json={"error_id": 502, "error_name": "throttle_violation",
                                         "error_message": "too many requests"})

    summary = run_stackexchange(repo, client=mock_client(handler))
    assert summary.status == "failed" and "throttle_violation" in summary.log[0]
    assert repo.runs_df().iloc[0]["status"] == "failed"


def test_search_plan_uses_custom_query(repo):
    topics = repo.topics_df()
    topics.loc[topics["name"] == "Related rates", "search_query"] = "related rates ladder"
    repo.update_topics(topics)
    plan = search_plan(repo).set_index("topic")
    assert plan.loc["Related rates", "query"] == "related rates ladder"
    assert plan.loc["Mean Value Theorem", "query"] == "Mean Value Theorem"
    assert plan.loc["Rank–nullity theorem", "query"] == "Rank nullity theorem"  # dash removed
    assert plan.loc["Mean Value Theorem", "tag"] == "calculus"
    assert plan.loc["Rank–nullity theorem", "tag"] == "linear-algebra"


def test_clean_query():
    from services.research import clean_query
    assert clean_query("Epsilon–delta definition of a limit") == "Epsilon delta definition of a limit"
    assert clean_query("  L'Hôpital's rule (0/0) ") == "L Hôpital s rule 0 0"


# ------------------------------------------------------------------ ranking
def test_ranking_signals(repo):
    topics = repo.topics_df().set_index("name")["topic_id"]
    mvt, rr = topics["Mean Value Theorem"], topics["Related rates"]
    for i in range(5):
        repo.add_source(source_type="stackexchange", title=f"s{i}", url=f"https://x.test/{i}",
                        engagement_count=40, topic_ids=[mvt])
    ranked = rank_topics(repo, now=datetime(2026, 10, 11)).set_index("topic_id")

    assert not ranked.loc[mvt, "evidence_missing"] and ranked.loc[rr, "evidence_missing"]
    assert ranked.loc[mvt, "observed_difficulty"] > ranked.loc[rr, "observed_difficulty"] == 0.3
    assert ranked.loc[mvt, "course_relevance"] == 1.0  # linked to a priority-5 course
    assert ranked.loc[mvt, "timeliness"] == 0.5  # no monitored term yet
    assert 0 <= ranked["final_score"].min() and ranked["final_score"].max() <= 100

    repo.add_offering("uoft_mat137", 2026, "Fall", monitor=True)
    assert rank_topics(repo, now=datetime(2026, 10, 11)).set_index("topic_id").loc[mvt, "timeliness"] == 1.0


def test_weights_change_order(repo):
    topics = repo.topics_df().set_index("name")["topic_id"]
    only_novelty = {k: 0.0 for k in ["course_relevance", "observed_difficulty", "timeliness",
                                     "content_quality_potential"]} | {"novelty": 1.0}
    assert set(rank_topics(repo, weights=only_novelty)["final_score"]) == {100.0}

    repo.add_questions([dict(topic_id=topics["Related rates"], question_latex="A ladder slides...",
                             question_type="computational", difficulty=2)],
                       batch_id="b", generation_model="m", prompt_version="v")
    ranked = rank_topics(repo, weights=only_novelty).set_index("topic_id")
    assert ranked.loc[topics["Related rates"], "final_score"] < 100  # open candidate lowers novelty


def test_selecting_a_question_marks_topic_used(repo):
    tid = repo.topics_df()["topic_id"].iloc[0]
    [qid] = repo.add_questions([dict(topic_id=tid, question_latex="Q text here", question_type="proof",
                                     difficulty=3)], batch_id="b", generation_model="m", prompt_version="v")
    repo.set_question_status(qid, selection_status="selected")
    assert repo.topics_df().set_index("topic_id").loc[tid, "last_used_at"] is not None
    assert rank_topics(repo).set_index("topic_id").loc[tid, "novelty"] == 0.0
