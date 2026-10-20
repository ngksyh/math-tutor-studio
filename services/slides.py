"""Render a post's slides to PNG images (KaTeX maths, headless Microsoft Edge).

The browser runs in a separate process (services/slide_worker.py) so it never interferes with
Streamlit's own event loop. Images go to data/slides/<post_id>/slide_N.png.
"""
from __future__ import annotations

import html
import io
import json
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from core.config import PROJECT_ROOT, load_config
from db.repository import Repository

KATEX_DIR = PROJECT_ROOT / "assets" / "katex"
SLIDES_ROOT = PROJECT_ROOT / "data" / "slides"
THEME_KEYS = ("handle", "background", "text", "accent")


@dataclass
class RenderResult:
    paths: list[Path] = field(default_factory=list)
    overflow: list[int] = field(default_factory=list)  # 1-based slide numbers that didn't fit
    error: str | None = None


def current_theme(repo: Repository) -> dict[str, str]:
    theme = {k: str(v) for k, v in load_config().get("slides", {}).get("theme", {}).items() if k in THEME_KEYS}
    theme.update({k: v for k, v in (repo.get_json_setting("slide_theme") or {}).items() if k in THEME_KEYS})
    return theme


def slide_size(platform: str) -> tuple[int, int]:
    w, h = load_config().get("slides", {}).get("sizes", {}).get(platform, [1080, 1350])
    return int(w), int(h)


def _paragraphs(text: str) -> str:
    """Plain text with $…$ maths → HTML paragraphs. Maths is left for KaTeX; **bold** is supported."""
    out = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        safe = html.escape(para, quote=False).replace("\n", "<br>")
        out.append("<p>" + re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe) + "</p>")
    return "".join(out)


QUESTION_CARD_TEXT = {"en": ("PROBLEM", "Try it before you swipe →"), "zh": ("题目", "先自己试一试，再往后翻 →")}


def question_card(question_latex: str, difficulty: int | None, language: str) -> dict:
    """A slide showing the exact stored question on the decorated 'problem' template."""
    return {"kind": "question", "body": question_latex, "difficulty": difficulty, "language": language}


def _question_section(s: dict, i: int, n: int, label: str, theme: dict[str, str]) -> str:
    pill, hint = QUESTION_CARD_TEXT.get(s.get("language", "en"), QUESTION_CARD_TEXT["en"])
    level = int(s.get("difficulty") or 0)
    dots = f'<span class="dots">{"●" * level}{"○" * (5 - level)}</span>' if 1 <= level <= 5 else ""
    return (f'<section class="slide question">'
            f'<div class="top"><span>{html.escape(label)}</span><span>{i} / {n}</span></div>'
            f'<div class="pill">{pill}{dots}</div>'
            f'<div class="box"><div class="body">{_paragraphs(s.get("body", ""))}</div></div>'
            f'<div class="hint">{hint}</div>'
            f'<div class="foot"><span>{html.escape(theme.get("handle", ""))}</span></div>'
            f"</section>")


def build_html(slides: list[dict], *, width: int, height: int, label: str, theme: dict[str, str]) -> str:
    n = len(slides)
    cards = []
    for i, s in enumerate(slides, start=1):
        if s.get("kind") == "question":
            cards.append(_question_section(s, i, n, label, theme))
            continue
        cards.append(
            f'<section class="slide{" cover" if i == 1 else ""}">'
            f'<div class="top"><span>{html.escape(label)}</span><span>{i} / {n}</span></div>'
            f'<h1>{_paragraphs(s.get("heading", "")).removeprefix("<p>").removesuffix("</p>")}</h1>'
            f'<div class="rule"></div>'
            f'<div class="body">{_paragraphs(s.get("body", ""))}</div>'
            f'<div class="foot"><span>{html.escape(theme.get("handle", ""))}</span></div>'
            f"</section>")
    k = KATEX_DIR.as_uri()
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="{k}/katex.min.css">
<script src="{k}/katex.min.js"></script><script src="{k}/auto-render.min.js"></script>
<style>
  body {{ margin: 0; background: #888; }}
  .slide {{ width: {width}px; height: {height}px; box-sizing: border-box; position: relative; overflow: hidden;
    padding: 88px 88px 128px; display: flex; flex-direction: column; margin-bottom: 20px;
    background: {theme.get("background", "#FAF8F3")}; color: {theme.get("text", "#1F2933")};
    font-family: "Microsoft YaHei", "Noto Sans SC", "PingFang SC", "Segoe UI", sans-serif; }}
  .top {{ display: flex; justify-content: space-between; font-size: 26px; letter-spacing: .04em;
    color: {theme.get("accent", "#2B5797")}; margin-bottom: 52px; font-weight: 600; }}
  h1 {{ font-size: 62px; line-height: 1.22; margin: 0 0 22px; font-weight: 700; }}
  .rule {{ width: 96px; height: 8px; border-radius: 4px; background: {theme.get("accent", "#2B5797")};
    margin-bottom: 44px; flex: none; }}
  .body {{ flex: 1; min-height: 0; overflow: hidden; font-size: 42px; line-height: 1.55; }}
  .body p {{ margin: 0 0 .65em; }}
  .katex {{ font-size: 1.05em; }}
  .katex-display {{ margin: .45em 0; }}
  .cover h1 {{ font-size: 80px; margin-top: 40px; }}
  .cover .body {{ font-size: 46px; }}
  .question .pill {{ align-self: flex-start; background: {theme.get("accent", "#2B5797")};
    color: {theme.get("background", "#FAF8F3")}; font-weight: 700; letter-spacing: .14em; font-size: 30px;
    padding: 12px 26px; border-radius: 999px; margin: 0 0 40px; }}
  .question .dots {{ margin-left: 18px; letter-spacing: .18em; font-size: 24px; }}
  .question .box {{ flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 40px 44px;
    border-left: 10px solid {theme.get("accent", "#2B5797")}; border-radius: 0 20px 20px 0;
    background: color-mix(in srgb, {theme.get("accent", "#2B5797")} 7%, transparent); }}
  .question .hint {{ margin-top: 28px; font-size: 30px; font-weight: 600; color: {theme.get("accent", "#2B5797")}; }}
  .foot {{ position: absolute; left: 88px; right: 88px; bottom: 52px; font-size: 26px; opacity: .65;
    border-top: 2px solid currentColor; padding-top: 18px; min-height: 30px; }}
</style></head><body>
{"".join(cards)}
<script>
document.addEventListener("DOMContentLoaded", async () => {{
  renderMathInElement(document.body, {{
    delimiters: [{{left: "$$", right: "$$", display: true}}, {{left: "$", right: "$", display: false}}],
    throwOnError: false }});
  await document.fonts.ready;
  for (const slide of document.querySelectorAll(".slide")) {{
    const body = slide.querySelector(".body");
    let size = parseFloat(getComputedStyle(body).fontSize);
    const over = () => body.scrollHeight > body.clientHeight + 1 || body.scrollWidth > body.clientWidth + 1;
    const set = (s) => {{ size = s; body.style.fontSize = s + "px"; }};
    // short text grows (up to 56px) so slides don't look empty; long text shrinks (down to 24px)
    while (!over() && size < 56) set(size + 2);
    while (over() && size > 24) set(size - 2);
    if (over()) slide.dataset.overflow = "1";
  }}
  window.__ready = true;
}});
</script></body></html>"""


def render_slides(slides: list[dict], out_dir: Path, *, platform: str, label: str,
                  theme: dict[str, str], timeout: int = 90) -> RenderResult:
    if not slides:
        return RenderResult(error="This post has no slides.")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("slide_*.png"):
        old.unlink()
    width, height = slide_size(platform)
    page = out_dir / "slides.html"
    page.write_text(build_html(slides, width=width, height=height, label=label, theme=theme), encoding="utf-8")
    job = {"html": str(page), "out_dir": str(out_dir), "width": width, "height": height,
           "channel": load_config().get("slides", {}).get("browser_channel", "msedge")}
    try:
        proc = subprocess.run([sys.executable, "-m", "services.slide_worker", json.dumps(job)],
                              cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return RenderResult(error=f"Rendering took longer than {timeout} seconds.")
    if proc.returncode != 0:
        return RenderResult(error=(proc.stderr or proc.stdout).strip()[-1500:] or "Rendering failed.")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    (out_dir / "slides.json").write_text(json.dumps(slides, ensure_ascii=False), encoding="utf-8")
    return RenderResult(paths=[Path(p) for p in result["paths"]], overflow=result["overflow"])


def post_label(repo: Repository, post: dict) -> str:
    """Top-left label on every slide, e.g. 'MAT137 · Mean Value Theorem'."""
    sol = repo.solutions_df().set_index("solution_id").loc[post["solution_id"]]
    question = repo.get_question(sol["question_id"])
    codes = question.get("course_codes") or []
    return " · ".join(x for x in (" / ".join(codes[:2]), question.get("topic")) if x)


def post_slides_dir(post_id: str) -> Path:
    return SLIDES_ROOT / post_id


def existing_images(post_id: str) -> list[Path]:
    return sorted(post_slides_dir(post_id).glob("slide_*.png"), key=lambda p: int(p.stem.split("_")[1]))


def render_input(repo: Repository, post: dict) -> list[dict]:
    """The slides actually rendered: the post's slides, with slide 2 replaced by the exact-question
    card when the post asks for it."""
    slides = [dict(s) for s in post["slides"] or []]
    if post.get("use_question_card") and slides:
        sol = repo.solutions_df().set_index("solution_id").loc[post["solution_id"]]
        question = repo.get_question(sol["question_id"])
        card = question_card(question["question_latex"], question.get("difficulty"), post["language"])
        if len(slides) >= 2:
            slides[1] = card
        else:
            slides.insert(1, card)
    return slides


def images_outdated(repo: Repository, post: dict) -> bool:
    """True if the slides (or the question-card choice) changed after the images were rendered."""
    record = post_slides_dir(post["post_id"]) / "slides.json"
    if not record.exists():
        return bool(existing_images(post["post_id"]))
    return json.loads(record.read_text(encoding="utf-8")) != render_input(repo, post)


def images_zip(post_id: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in existing_images(post_id):
            zf.write(path, path.name)
    return buffer.getvalue()


def render_post(repo: Repository, post: dict) -> RenderResult:
    return render_slides(render_input(repo, post), post_slides_dir(post["post_id"]), platform=post["platform"],
                         label=post_label(repo, post), theme=current_theme(repo))
