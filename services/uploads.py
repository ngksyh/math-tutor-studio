"""Uploaded course documents (PDF, images, text) and how they reach the AI.

The original file is kept under data/imports/ (git-ignored) next to its extracted text, so each
round can choose the best route per document:

- API mode, provider reads files (Claude, OpenAI): the file itself is sent.
- Otherwise, if text could be extracted (PDF, .txt, .md): the text goes into the prompt.
- ChatGPT copy-and-paste mode, no text (e.g. a scan): you attach the file in ChatGPT yourself.
- API mode with a text-only provider and no text: not possible — the app says so.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

from collectors.page_reader import Document, pdf_to_document, pasted_document
from core.config import PROJECT_ROOT
from db.repository import Repository

IMPORT_DIR = PROJECT_ROOT / "data" / "imports"
MAX_MB = 20
MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "txt": "text/plain", "md": "text/plain"}
UPLOAD_TYPES = list(MIME)


@dataclass
class FilePart:
    """A file sent to an AI provider alongside the prompt."""
    name: str
    mime: str
    data: bytes

    @property
    def b64(self) -> str:
        return base64.standard_b64encode(self.data).decode("ascii")


@dataclass
class DocumentRoute:
    """How one chosen document reaches the AI in this round."""
    doc: dict
    route: str  # "file" (sent as a file) / "text" (inline) / "attach" (you attach in ChatGPT) / "impossible"
    part: FilePart | None = None


@dataclass
class RoutePlan:
    routes: list[DocumentRoute] = field(default_factory=list)

    def by(self, route: str) -> list[DocumentRoute]:
        return [r for r in self.routes if r.route == route]


def extension(name: str) -> str:
    return Path(name).suffix.lower().lstrip(".")


def read_upload(name: str, data: bytes) -> Document:
    """Extract what text we can from an uploaded file (images have none)."""
    ext = extension(name)
    if ext not in MIME:
        raise ValueError(f"Unsupported file type .{ext}. Use PDF, PNG/JPG/WEBP, TXT or MD.")
    if len(data) > MAX_MB * 1024 * 1024:
        raise ValueError(f"The file is larger than {MAX_MB} MB. Upload just the pages you need.")
    if ext == "pdf":
        doc = pdf_to_document(None, data)
        doc.title = name
        return doc
    if MIME[ext].startswith("text/"):
        return pasted_document(data.decode("utf-8", errors="replace"), name)
    return Document(url=None, title=name, text="", kind="image",
                    warnings=["Images have no extractable text: in API mode Claude or OpenAI can read them "
                              "directly; in ChatGPT mode you'll attach the file yourself."])


def store_file(repo: Repository, source_id: str, name: str, data: bytes) -> str:
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = IMPORT_DIR / f"{source_id}.{extension(name)}"
    path.write_bytes(data)
    relative = path.relative_to(PROJECT_ROOT).as_posix()
    repo.set_source_file(source_id, relative)
    return relative


def load_part(doc: dict) -> FilePart | None:
    rel = doc.get("file_path")
    if not isinstance(rel, str) or not rel:
        return None
    path = PROJECT_ROOT / rel
    if not path.exists():
        return None
    return FilePart(name=f"{doc['title']}", mime=MIME.get(extension(path.name), "application/octet-stream"),
                    data=path.read_bytes())


def plan_routes(documents: list[dict], *, accepts: set[str], manual: bool) -> RoutePlan:
    """Decide per document how it reaches the AI. `accepts`: MIME types/prefixes the provider reads."""
    plan = RoutePlan()
    for doc in documents:
        part = load_part(doc)
        has_text = bool((doc.get("full_text") or "").strip())
        if part and not part.mime.startswith("text/") and \
                any(part.mime == a or part.mime.startswith(a) for a in accepts):
            plan.routes.append(DocumentRoute(doc, "file", part))
        elif has_text:
            plan.routes.append(DocumentRoute(doc, "text"))
        elif part and manual:
            plan.routes.append(DocumentRoute(doc, "attach", part))
        else:
            plan.routes.append(DocumentRoute(doc, "impossible", part))
    return plan
