import json

import httpx
import pytest

from collectors.page_reader import ReadError, fetch_document, html_to_document, pasted_document
from db.repository import Repository
from db.seed import seed_initial_data
from services.prompting import ReplyFormatError
from services.topic_suggester import (
    add_suggested_topics,
    build_topics_prompt,
    parse_topic_suggestions,
    save_document,
)

SYLLABUS_HTML = """<html><head><title>MAT137 Syllabus</title><script>var x=1;</script></head>
<body><nav>Home | Login</nav><h1>MAT137: Calculus with Proofs</h1>
<p>Week 3: limits and the epsilon-delta definition.</p><ul><li>Week 7: Mean Value Theorem</li>
<li>Week 9: Taylor polynomials and error bounds</li><li>Week 11: improper integrals</li></ul>
<footer>Copyright</footer></body></html>"""


def minimal_pdf(text: str) -> bytes:
    """A tiny valid one-page PDF containing `text` (for testing extraction)."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def client_returning(content: bytes, content_type: str, status: int = 200) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, content=content, headers={"content-type": content_type})))


@pytest.fixture
def repo(tmp_path):
    r = Repository(tmp_path / "t.db")
    seed_initial_data(r)
    return r


# ------------------------------------------------------------ page reader
def test_html_extraction_drops_scripts_and_navigation():
    doc = html_to_document("https://x.test/s", SYLLABUS_HTML)
    assert doc.title == "MAT137 Syllabus"
    assert "Mean Value Theorem" in doc.text and "Taylor polynomials" in doc.text
    assert "var x" not in doc.text and "Login" not in doc.text and "Copyright" not in doc.text


def test_fetch_html_and_pdf():
    html_doc = fetch_document("https://x.test/s", client=client_returning(SYLLABUS_HTML.encode(), "text/html"))
    assert html_doc.kind == "html" and "epsilon-delta" in html_doc.text
    pdf_doc = fetch_document("https://x.test/syllabus.pdf",
                             client=client_returning(minimal_pdf("Week 5 Rank nullity theorem"), "application/pdf"))
    assert pdf_doc.kind == "pdf" and "Rank nullity theorem" in pdf_doc.text


def test_fetch_errors_and_login_warning():
    with pytest.raises(ReadError):
        fetch_document("not a url")
    with pytest.raises(ReadError):
        fetch_document("https://x.test/s", client=client_returning(b"", "text/html", status=404))
    login = fetch_document("https://q.test", client=client_returning(
        b"<html><body><h1>Sign in to Quercus</h1><form>user</form></body></html>", "text/html"))
    assert any("login page" in w for w in login.warnings)


# --------------------------------------------------------------- suggester
def reply(*topics, **extra):
    return json.dumps({"task": "TOPICS", "course_id": "uoft_mat137", "topics": list(topics), "notes": "", **extra})


def topic(name, **kw):
    return {"name": name, "subject": "Calculus", "target_level": "intro_proofs", "difficulty_level": 3,
            "search_query": "taylor remainder bound!", "evidence_type": "listed",
            "syllabus_evidence": "Week 9: Taylor polynomials", **kw}


def doc_row(title, text, source_type="syllabus"):
    return {"title": title, "source_type": source_type, "url": None, "full_text": text}


def test_prompt_has_anchors_and_typed_documents(repo):
    docs = [doc_row("MAT137 syllabus", "Week 9: Chapter 11, sections 11.10-11.11."),
            doc_row("Stewart 9e contents", "11.10 Taylor and Maclaurin Series\n11.11 Applications of Taylor Polynomials",
                    "textbook_contents")]
    prompt = build_topics_prompt(repo, "uoft_mat137", docs, max_topics=8, include_rules=False)
    assert prompt.startswith("Task: TOPICS") and "UofT MAT137" in prompt and "at most 8 topics" in prompt
    # MAT137's own topics are sent with difficulty as anchors; other courses' topics by name only
    assert "- Mean Value Theorem (difficulty 3)" in prompt
    assert "- Related rates" in prompt and "Related rates (difficulty" not in prompt
    assert "Document 1: Syllabus / course page — MAT137 syllabus" in prompt
    assert "Document 2: Textbook contents (table of contents) — Stewart 9e contents" in prompt
    with pytest.raises(ValueError):
        build_topics_prompt(repo, "uoft_mat137", [], max_topics=5, include_rules=False)


def test_budget_keeps_short_documents_whole():
    from services.topic_suggester import _fit_budget
    short, long_ = "a" * 100, "b" * 50_000
    out = _fit_budget([short, long_], 1000)
    assert out[0] == short and out[1].startswith("b" * 900) and "truncated" in out[1]
    assert _fit_budget(["x" * 10, "y" * 10], 1000) == ["x" * 10, "y" * 10]


def test_parse_flags_duplicates_implied_and_cleans(repo):
    result = parse_topic_suggestions(repo, reply(
        topic("Taylor polynomial error bounds"),
        topic("Mean value theorem"),                  # already exists (case differs)
        topic("Taylor polynomial error bounds"),      # repeated
        {"name": "x"},                                # invalid
        topic("Improper integrals", target_level="nonsense", evidence_type="from memory"),
        topic("Cauchy sequences", evidence_type="textbook_contents"),
    ), course_id="uoft_mat137")
    rows = {r["name"]: r for r in result.topics}
    assert rows["Taylor polynomial error bounds"]["add"]
    assert rows["Taylor polynomial error bounds"]["search_query"] == "taylor remainder bound"
    assert not rows["Mean value theorem"]["add"] and rows["Mean value theorem"]["similar_to"] == "Mean Value Theorem"
    imp = rows["Improper integrals"]
    assert imp["target_level"] == "university_intro" and imp["evidence_type"] == "implied" and not imp["add"]
    assert rows["Cauchy sequences"]["add"] and rows["Cauchy sequences"]["evidence_type"] == "textbook_contents"
    assert len(result.rejected) == 2


def test_wrong_task_rejected(repo):
    with pytest.raises(ReplyFormatError):
        parse_topic_suggestions(repo, json.dumps({"task": "DRAFTS", "topics": []}), course_id="uoft_mat137")


def test_add_topics_and_save_documents(repo):
    result = parse_topic_suggestions(repo, reply(topic("Taylor polynomial error bounds")), course_id="uoft_mat137")
    [tid] = add_suggested_topics(repo, result.topics, course_id="uoft_mat137")
    row = repo.topics_df().set_index("topic_id").loc[tid]
    assert row["course_ids"] == ["uoft_mat137"]
    assert row["search_query"] == "taylor remainder bound"

    toc = pasted_document("11.10 Taylor and Maclaurin Series\n" * 60, "Stewart contents")
    sid = save_document(repo, toc, course_id="uoft_mat137", offering_id=None, doc_type="textbook_contents",
                        title="Stewart 9e — contents")
    again = save_document(repo, toc, course_id="uoft_mat137", offering_id=None, doc_type="textbook_contents")
    assert again == sid  # same document isn't stored twice
    docs = repo.course_documents_df("uoft_mat137")
    assert list(docs["title"]) == ["Stewart 9e — contents"] and docs.iloc[0]["full_text"] == toc.text
    assert repo.course_documents_df("ubc_math100").empty
    src = repo.sources_df().set_index("source_id").loc[sid]
    assert src["source_type"] == "textbook_contents" and src["topic_ids"] == []  # coverage, not difficulty
    with pytest.raises(ValueError):
        save_document(repo, toc, course_id="uoft_mat137", offering_id=None, doc_type="exam")


def test_clean_contents_strips_leaders_and_page_numbers():
    from collectors.page_reader import clean_contents
    toc = ("1 LogicandProofs 5\n1.1 Setsandnotation. . . . . . . . . 5\n1\n"
           "4.3 TheMeanValueTheorem. . . . . . . . . 89\n10PowerSeries 186\n10.1TaylorSeries . . . . 186\n"
           "Week 9: Chapter 11")
    assert clean_contents(toc).splitlines() == [
        "1 LogicandProofs", "1.1 Setsandnotation", "4.3 TheMeanValueTheorem", "10PowerSeries", "10.1TaylorSeries",
        "Week 9: Chapter 11"]  # non-numbered lines keep their numbers


def test_course_notes_scope_notes_and_unmapped(repo):
    notes_doc = pasted_document("4.3 TheMeanValueTheorem . . . . . 89\n" * 40, "MAT137 notes")
    sid_notes = save_document(repo, notes_doc, course_id="uoft_mat137", offering_id=None, doc_type="course_notes")
    book = pasted_document("9.2 Limits of sequences . . . . 171\n" * 40, "Book contents")
    sid_book = save_document(repo, book, course_id="uoft_mat137", offering_id=None, doc_type="textbook_contents",
                             scope="1-6, 9")
    docs = repo.course_documents_df("uoft_mat137").set_index("source_id")
    assert ". . ." not in docs.loc[sid_notes, "full_text"] and "89" not in docs.loc[sid_notes, "full_text"]
    assert docs.loc[sid_book, "doc_scope"] == "1-6, 9"

    prompt = build_topics_prompt(repo, "uoft_mat137", docs.reset_index().to_dict("records"), max_topics=10,
                                 include_rules=False, notes="These are the official MAT137 notes.")
    assert "Scope: the course's own notes/outline" in prompt
    assert "Chapters/sections in scope (from the tutor): 1-6, 9" in prompt
    assert "## Tutor's notes about these documents\nThese are the official MAT137 notes." in prompt

    repo.set_document_scope(sid_book, "  ")
    assert repo.course_documents_df("uoft_mat137").set_index("source_id").loc[sid_book, "doc_scope"] is None

    result = parse_topic_suggestions(repo, reply(topic("Monotone convergence theorem", evidence_type="unmapped")),
                                     course_id="uoft_mat137")
    assert result.topics[0]["evidence_type"] == "unmapped" and not result.topics[0]["add"]


def test_change_document_type_cleans_text(repo):
    from services.topic_suggester import update_document
    raw = pasted_document("4.3 TheMeanValueTheorem . . . . . 89\n" * 40, "Contents")
    sid = save_document(repo, raw, course_id="uoft_mat137", offering_id=None, doc_type="other")
    row = repo.course_documents_df("uoft_mat137").set_index("source_id").loc[sid]
    assert ". . ." in row["full_text"]  # "other" documents are stored as given
    update_document(repo, sid, doc_type="course_notes", scope=None, full_text=row["full_text"])
    row = repo.course_documents_df("uoft_mat137").set_index("source_id").loc[sid]
    assert row["source_type"] == "course_notes" and ". . ." not in row["full_text"]


def test_attachments_in_chatgpt(repo):
    from services.topic_suggester import record_attachment
    att = [{"title": "MAT137 notes pages 3-5 (scan)", "source_type": "course_notes", "doc_scope": None}]
    prompt = build_topics_prompt(repo, "uoft_mat137", [], max_topics=5, include_rules=False, attachments=att)
    assert "Document 1 (attached to this chat message): Course notes / outline" in prompt
    assert "MAT137 notes pages 3-5 (scan)" in prompt and "file attached to this chat message" in prompt
    assert "Scope: the course's own notes/outline" in prompt

    saved = pasted_document("4.3 The Mean Value Theorem\n" * 40, "Contents")
    save_document(repo, saved, course_id="uoft_mat137", offering_id=None, doc_type="course_notes")
    docs = repo.course_documents_df("uoft_mat137").to_dict("records")
    both = build_topics_prompt(repo, "uoft_mat137", docs, max_topics=5, include_rules=False, attachments=att)
    assert "## Document 1: Course notes" in both and "## Document 2 (attached to this chat message)" in both

    sid = record_attachment(repo, course_id="uoft_mat137", offering_id=None, doc_type="course_notes",
                            title="MAT137 notes pages 3-5 (scan)")
    src = repo.sources_df().set_index("source_id").loc[sid]
    assert src["title"].startswith("Attached in ChatGPT:") and src["course_id"] == "uoft_mat137"
    assert sid not in set(repo.course_documents_df("uoft_mat137")["source_id"])  # no text, so not reusable
