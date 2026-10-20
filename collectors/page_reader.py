"""Read one public web page or PDF (a syllabus, course page, schedule) into plain text.

Only fetches the single URL you give it, when you click — no crawling. Pages behind a login
(Quercus, Canvas) can't be read; paste their text instead.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx

MAX_BYTES = 15_000_000
MAX_PDF_PAGES = 40
LOGIN_HINTS = ("sign in", "log in", "login", "single sign-on", "authenticate", "weblogin")


class ReadError(RuntimeError):
    pass


@dataclass
class Document:
    url: str | None
    title: str
    text: str
    kind: str  # html / pdf / pasted
    warnings: list[str] = field(default_factory=list)


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "button"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table",
             "ul", "ol", "dd", "dt"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):  # noqa: ANN001
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def tidy(text: str) -> str:
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


_DOT_LEADER = re.compile(r"\s*(?:[.·…]\s*){2,}\s*\d{1,4}\s*$")  # "Title . . . . . 89"
_SECTION_LINE = re.compile(r"^\d+(?:\.\d+)*\.?\s*\D")  # numbered entries: "4.3 …", "10PowerSeries"
_TRAILING_PAGE = re.compile(r"\s+\d{1,4}\s*$")


def clean_contents(text: str) -> str:
    """Strip dot leaders, page numbers and stray page-number lines from a table of contents.
    These are often more than half the characters, so removing them saves prompt space."""
    out = []
    for line in text.splitlines():
        line = _DOT_LEADER.sub("", line).rstrip()
        if re.fullmatch(r"\s*\d{1,4}\s*", line):  # a bare page number (page footer)
            continue
        if _SECTION_LINE.match(line):
            line = _TRAILING_PAGE.sub("", line)
        out.append(line)
    return tidy("\n".join(out))


def _avg_word_length(text: str) -> float:
    words = text.split()
    return sum(map(len, words)) / len(words) if words else 0.0


def html_to_document(url: str | None, html: str) -> Document:
    parser = _TextExtractor()
    parser.feed(html)
    return Document(url=url, title=tidy(parser.title) or (url or "Web page"), text=tidy("".join(parser.parts)),
                    kind="html")


def pdf_to_document(url: str | None, data: bytes) -> Document:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages[:MAX_PDF_PAGES]
    text = tidy("\n\n".join(page.extract_text() or "" for page in pages))
    # Many LaTeX PDFs lose the spaces between words ("TheMeanValueTheorem"); layout mode often keeps
    # them. Use it when it gives clearly shorter average words.
    try:
        layout = tidy("\n\n".join(page.extract_text(extraction_mode="layout") or "" for page in pages))
        if layout and _avg_word_length(layout) < 0.85 * _avg_word_length(text):
            text = layout
    except Exception:  # noqa: BLE001 — layout mode is a best-effort improvement
        pass
    doc = Document(url=url, title=(reader.metadata.title if reader.metadata and reader.metadata.title else None)
                   or (url.rsplit("/", 1)[-1] if url else "PDF"), text=text, kind="pdf")
    if len(reader.pages) > MAX_PDF_PAGES:
        doc.warnings.append(f"Only the first {MAX_PDF_PAGES} of {len(reader.pages)} pages were read.")
    if not text:
        doc.warnings.append("No text found — the PDF may be scanned images. Paste the text instead.")
    return doc


def check_quality(doc: Document) -> Document:
    words = len(doc.text.split())
    head = doc.text[:2000].lower()
    if words < 80:
        doc.warnings.append(f"Only {words} words were found.")
    if any(h in head for h in LOGIN_HINTS) and words < 400:
        doc.warnings.append("This looks like a login page — course sites behind a login can't be read. "
                            "Open it in your browser and paste the syllabus text instead.")
    return doc


def fetch_document(url: str, *, client: httpx.Client | None = None) -> Document:
    url = url.strip()
    if not re.match(r"https?://", url):
        raise ReadError("Enter a full link starting with http:// or https://")
    client = client or httpx.Client(timeout=30, follow_redirects=True,
                                    headers={"User-Agent": "Mozilla/5.0 (MathTutorStudio; syllabus reader)"})
    try:
        resp = client.get(url)
    except httpx.HTTPError as e:
        raise ReadError(f"Couldn't reach the page: {e}") from e
    if resp.status_code >= 400:
        raise ReadError(f"The site answered HTTP {resp.status_code}.")
    if len(resp.content) > MAX_BYTES:
        raise ReadError("The file is too large (over 15 MB).")
    ctype = resp.headers.get("content-type", "").lower()
    if "pdf" in ctype or resp.content[:5] == b"%PDF-":
        doc = pdf_to_document(str(resp.url), resp.content)
    elif "html" in ctype or "text" in ctype or not ctype:
        doc = html_to_document(str(resp.url), resp.text)
    else:
        raise ReadError(f"Unsupported file type: {ctype}. Use a web page or PDF, or paste the text.")
    return check_quality(doc)


def pasted_document(text: str, title: str) -> Document:
    return check_quality(Document(url=None, title=title, text=tidy(text), kind="pasted"))
