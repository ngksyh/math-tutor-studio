import json

import pytest

from db.repository import Repository
from db.seed import seed_initial_data
from services.question_generator import (
    RULES_VERSION,
    rules_text,
    ReplyFormatError,
    build_prompt,
    extract_json,
    save_accepted,
    validate_reply,
)


@pytest.fixture
def repo(tmp_path):
    r = Repository(tmp_path / "t.db")
    seed_initial_data(r)
    return r


@pytest.fixture
def ids(repo):
    topics = repo.topics_df().set_index("name")["topic_id"]
    mvt, span = topics["Mean Value Theorem"], topics["Linear independence and span"]
    src, _ = repo.add_source(source_type="stackexchange", title="MVT confusion", url="https://x.test/1",
                             engagement_count=12, topic_ids=[mvt])
    return {"mvt": mvt, "span": span, "src": src}


def q(topic_id, latex="Show that $|\\sin a - \\sin b| \\le |a-b|$ for all real $a, b$.", **kw):
    base = dict(topic_id=topic_id, title="Sine is 1-Lipschitz", question_latex=latex,
                question_type="proof", difficulty=2, expected_time_min=10,
                target_audience="intro_proofs", proposed_solution="Apply the MVT.",
                teaching_insight="Bounding derivatives gives Lipschitz bounds.", source_ids=[])
    return {**base, **kw}


def reply(*questions, batch_id="batch_x"):
    return json.dumps({"batch_id": batch_id, "questions": list(questions)})


# ------------------------------------------------------------------ parsing
def test_extract_json_tolerates_fence_and_prose():
    text = 'Sure! Here you go:\n```json\n{"batch_id": "b", "questions": []}\n```\nGood luck!'
    assert extract_json(text) == {"batch_id": "b", "questions": []}


def test_extract_json_accepts_bare_list():
    assert extract_json('[{"a": 1}]') == {"questions": [{"a": 1}]}


def test_unescaped_latex_is_repaired_not_corrupted():
    # Single backslashes as ChatGPT often writes them: \f \t \n \b \r would otherwise become
    # form-feed / tab / newline / backspace / carriage-return characters.
    raw = r'{"questions": [{"x": "$\frac{1}{2}$, \theta \to 0, \nabla f, \beta, \right), \alpha, \cup"}]}'
    x = extract_json(raw)["questions"][0]["x"]
    assert x == r"$\frac{1}{2}$, \theta \to 0, \nabla f, \beta, \right), \alpha, \cup"


def test_correct_json_is_unchanged():
    data = {"questions": [{"x": "line one\nline two\t\"quoted\" \\frac{a}{b} é"}]}
    assert extract_json(json.dumps(data)) == data


def test_garbage_reply_raises_helpful_error():
    with pytest.raises(ReplyFormatError):
        extract_json("I can't help with that.")
    with pytest.raises(ReplyFormatError):
        extract_json('{"questions": [ {"broken": } ]}')


# --------------------------------------------------------------- validation
def test_valid_question_accepted_and_saved(repo, ids):
    report = validate_reply(repo, reply(q(ids["mvt"], source_ids=[ids["src"]])), expected_batch_id="batch_x")
    assert len(report.accepted) == 1 and not report.rejected and not report.warnings
    [qid] = save_accepted(repo, report, batch_id="batch_x", generation_model="chatgpt-manual")
    row = repo.questions_df().set_index("question_id").loc[qid]
    assert row["review_status"] == "unreviewed" and row["selection_status"] == "candidate"
    assert row["batch_id"] == "batch_x" and "\\sin" not in row["question_plaintext"]


def test_invented_and_mismatched_sources_rejected(repo, ids):
    report = validate_reply(repo, reply(
        q(ids["mvt"], source_ids=["src_fake"]),
        q(ids["span"], latex="Are $(1,0)$ and $(0,1)$ a basis of $\\mathbb R^2$? Explain.", source_ids=[ids["src"]]),
    ))
    assert not report.accepted
    reasons = [r.reasons[0] for r in report.rejected]
    assert "does not exist" in reasons[0] and "not evidence for this topic" in reasons[1]


def test_schema_errors_and_unknown_topic_rejected(repo, ids):
    report = validate_reply(repo, reply(
        q(ids["mvt"], difficulty=9),
        q(ids["mvt"], question_type="essay"),
        q("top_nope"),
        {"title": "missing everything"},
    ))
    assert not report.accepted and len(report.rejected) == 4
    assert "unknown topic_id" in report.rejected[2].reasons[0]


def test_duplicates_rejected_within_reply_and_against_database(repo, ids):
    report = validate_reply(repo, reply(q(ids["mvt"]), q(ids["mvt"], title="same again")))
    assert len(report.accepted) == 1 and "this reply" in report.rejected[0].reasons[0]
    save_accepted(repo, report, batch_id="b1", generation_model="m")
    again = validate_reply(repo, reply(q(ids["mvt"], latex="Show that $|\\sin a-\\sin b|\\le|a-b|$ for all real $a,b$.")))
    assert not again.accepted and "already in your database" in again.rejected[0].reasons[0]


def test_batch_mismatch_is_a_warning_only(repo, ids):
    report = validate_reply(repo, reply(q(ids["mvt"]), batch_id="batch_old"), expected_batch_id="batch_new")
    assert report.accepted and report.warnings


# ------------------------------------------------------------------- prompt
def test_prompt_lists_topics_evidence_and_batch(repo, ids):
    prompt = build_prompt(repo, [ids["mvt"], ids["span"]], 5, "batch_123")
    assert "batch_123" in prompt and ids["mvt"] in prompt and ids["span"] in prompt
    assert f"Evidence {ids['src']}" in prompt and "12 votes" in prompt
    assert "none collected yet" in prompt  # span has no sources
    assert "<<" not in prompt  # all placeholders filled


def test_short_prompt_omits_rules_but_keeps_preferences(repo, ids):
    full = build_prompt(repo, [ids["mvt"]], 3, "b1", preferences="Harder analysis please.")
    short = build_prompt(repo, [ids["mvt"]], 3, "b1", include_rules=False, preferences="Harder analysis please.")
    assert "Task: QUESTIONS — write" in full and "write original candidate" not in short
    assert "project's instructions" in short and RULES_VERSION in short
    assert short.startswith("Task: QUESTIONS") and full.startswith("Task: QUESTIONS")
    for p in (full, short):
        assert "Harder analysis please." in p and ids["mvt"] in p and "b1" in p and "<<" not in p
    assert len(short) < len(full) / 2


def test_rules_text_is_complete():
    rules = rules_text()
    assert "<<" not in rules and RULES_VERSION in rules and "gre_math_subject" in rules
    assert "Task: MATH_REVIEW" in rules and "Task: DRAFTS" in rules
    assert "NEVER use" in rules  # delimiter rule


def test_import_normalizes_delimiters(repo, ids):
    report = validate_reply(repo, reply(q(ids["mvt"], latex=r"Prove \(\frac{h}{6} > 0\) for \[h>0.\]")))
    [qid] = save_accepted(repo, report, batch_id="b", generation_model="m")
    stored = repo.questions_df().set_index("question_id").loc[qid, "question_latex"]
    assert stored == r"Prove $\frac{h}{6} > 0$ for $$h>0.$$"


def test_edit_question_text(repo, ids):
    report = validate_reply(repo, reply(q(ids["mvt"])))
    [qid] = save_accepted(repo, report, batch_id="b", generation_model="m")
    repo.update_question_text(qid, question_latex=r"Let $A=\begin{pmatrix}1&0\\0&1\end{pmatrix}$. Is it invertible?")
    assert "pmatrix" in repo.questions_df().set_index("question_id").loc[qid, "question_latex"]
    with pytest.raises(Exception):
        repo.update_question_text(qid, selection_status="selected")
