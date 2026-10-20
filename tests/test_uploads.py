"""Uploaded course documents: text extraction, storage, routing, and sending to providers."""
from types import SimpleNamespace

import pytest

import services.uploads as uploads
from db.repository import Repository
from db.seed import seed_initial_data
from services import ai_client, claude_client
from services.topic_suggester import build_topics_prompt, save_document
from services.uploads import FilePart, plan_routes, read_upload, store_file
from tests.test_topic_suggester import minimal_pdf

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # bytes are enough: images aren't decoded by the app


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "IMPORT_DIR", tmp_path / "imports")
    monkeypatch.setattr(uploads, "PROJECT_ROOT", tmp_path)
    r = Repository(tmp_path / "t.db")
    seed_initial_data(r)
    return r


def save(repo, name, data, doc_type="course_notes"):
    doc = read_upload(name, data)
    sid = save_document(repo, doc, course_id="uoft_mat137", offering_id=None, doc_type=doc_type)
    store_file(repo, sid, name, data)
    return sid


def test_read_upload_kinds():
    assert "Rank nullity" in read_upload("notes.pdf", minimal_pdf("Week 5 Rank nullity theorem")).text
    assert read_upload("toc.txt", "4.3 The Mean Value Theorem".encode()).text == "4.3 The Mean Value Theorem"
    image = read_upload("scan.png", PNG)
    assert image.text == "" and image.kind == "image" and image.warnings
    with pytest.raises(ValueError):
        read_upload("slides.pptx", b"x")


def test_uploaded_documents_are_listed_with_file(repo):
    sid_pdf = save(repo, "notes.pdf", minimal_pdf("Week 5 Rank nullity theorem"))
    sid_png = save(repo, "scan.png", PNG)
    docs = repo.course_documents_df("uoft_mat137").set_index("source_id")
    assert docs.loc[sid_pdf, "file_path"].endswith(f"{sid_pdf}.pdf") and docs.loc[sid_pdf, "words"] > 0
    assert docs.loc[sid_png, "file_path"].endswith(f"{sid_png}.png") and docs.loc[sid_png, "words"] == 0
    assert (uploads.IMPORT_DIR / f"{sid_png}.png").read_bytes() == PNG


def test_routes_depend_on_provider(repo):
    save(repo, "notes.pdf", minimal_pdf("Week 5 Rank nullity theorem"))
    save(repo, "scan.png", PNG)
    pasted = read_upload("toc.txt", b"4.3 The Mean Value Theorem")
    save_document(repo, pasted, course_id="uoft_mat137", offering_id=None, doc_type="course_notes")
    docs = repo.course_documents_df("uoft_mat137").to_dict("records")
    kinds = lambda plan: {r.doc["title"]: r.route for r in plan.routes}  # noqa: E731

    claude = kinds(plan_routes(docs, accepts={"application/pdf", "image/"}, manual=False))
    assert claude == {"notes.pdf": "file", "scan.png": "file", "toc.txt": "text"}
    deepseek = kinds(plan_routes(docs, accepts=set(), manual=False))
    assert deepseek == {"notes.pdf": "text", "scan.png": "impossible", "toc.txt": "text"}
    chatgpt = kinds(plan_routes(docs, accepts=set(), manual=True))
    assert chatgpt == {"notes.pdf": "text", "scan.png": "attach", "toc.txt": "text"}


def test_prompt_labels_sent_files(repo):
    save(repo, "scan.png", PNG)
    docs = repo.course_documents_df("uoft_mat137").to_dict("records")
    prompt = build_topics_prompt(repo, "uoft_mat137", [], max_topics=5, include_rules=False, sent_files=docs)
    assert "Document 1 (sent with this message as a file): Course notes / outline (the course's own) — scan.png" in prompt


def test_claude_receives_document_and_image_blocks(monkeypatch):
    calls = []
    response = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="{}")])
    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: (calls.append(kw), response)[1])))
    monkeypatch.setattr(claude_client, "_client", lambda key: fake)
    files = [FilePart("notes", "application/pdf", b"%PDF-1.4"), FilePart("scan", "image/png", PNG)]
    claude_client.complete(api_key="k", model="claude-opus-5-5", system="S", prompt="P", files=files)
    content = calls[0]["messages"][0]["content"]
    assert [b["type"] for b in content] == ["document", "image", "text"]  # files before the text
    assert content[0]["source"]["media_type"] == "application/pdf" and content[0]["title"] == "notes"
    assert content[1]["source"]["type"] == "base64" and content[2]["text"] == "P"


def test_openai_receives_file_and_image_parts(monkeypatch):
    calls = []

    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}"))])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(ai_client, "get_client", lambda api_key, base_url=None: fake)
    files = [FilePart("notes", "application/pdf", b"%PDF-1.4"), FilePart("scan", "image/png", PNG)]
    ai_client.complete(api_key="k", base_url=None, model="gpt-x", system="S", prompt="P", files=files)
    user = calls[0]["messages"][1]["content"]
    assert [p["type"] for p in user] == ["text", "file", "image_url"]
    assert user[1]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert user[2]["image_url"]["url"].startswith("data:image/png;base64,")
