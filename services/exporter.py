"""Export a publication package: your solution plus each platform's post, as Markdown files."""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from pathlib import Path

from core.config import PROJECT_ROOT
from db.repository import Repository
from services.draft_generator import copy_text
from services.slides import existing_images

EXPORT_ROOT = PROJECT_ROOT / "data" / "exports"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "post").lower()).strip("-")[:50] or "post"


def _post_markdown(post: dict) -> str:
    lines = [f"# {post['platform'].title()} ({post['language']}) — {post['status']}", "",
             "## Copy-ready text", "", "```", copy_text(post), "```", ""]
    if post.get("title_options"):
        lines += ["## Title options", *[f"- {t}" for t in post["title_options"]], ""]
    if post.get("keywords"):
        lines += ["## Search keywords", ", ".join(post["keywords"]), ""]
    if post.get("slides"):
        lines += ["## Slides", ""]
        for i, s in enumerate(post["slides"], start=1):
            lines += [f"### {i}. {s.get('heading', '')}", s.get("body", ""), ""]
    if post.get("flags"):
        lines += ["## Open warnings", *[f"- {f}" for f in post["flags"]], ""]
    return "\n".join(lines)


def build_package(repo: Repository, solution_id: str) -> tuple[str, dict[str, str | bytes]]:
    """Returns (folder name, {filename: content}); slide images are included when rendered."""
    sol_row = repo.solutions_df().set_index("solution_id").loc[solution_id]
    question, solution = repo.get_question(sol_row["question_id"]), repo.get_solution(sol_row["question_id"])
    files = {"solution.md": "\n\n".join(x for x in (
        f"# {question['title']}", f"Topic: {question['topic']}",
        "## Problem", question["question_latex"],
        "## Solution (yours)", solution["raw_reasoning"],
        solution.get("intuition") and f"## Key intuition\n{solution['intuition']}",
        solution.get("common_mistakes") and f"## Common mistakes\n{solution['common_mistakes']}",
        solution.get("alternate_methods") and f"## Alternative methods\n{solution['alternate_methods']}",
    ) if x)}
    for post in repo.posts_df(solution_id).to_dict("records"):
        if post["status"] in ("rejected", "archived"):
            continue
        name = f"{post['platform']}.md"
        if name in files:  # several rounds for one platform
            name = f"{post['platform']}_{post['post_id'][-6:]}.md"
        files[name] = _post_markdown(post)
        for image in existing_images(post["post_id"]):
            files[f"{name.removesuffix('.md')}_slides/{image.name}"] = image.read_bytes()
    return f"{date.today():%Y-%m-%d}_{_slug(question['title'])}", files


def export_package(repo: Repository, solution_id: str, root: Path = EXPORT_ROOT) -> tuple[Path, bytes]:
    """Write the package to data/exports/<date>_<title>/ and return (folder, zip bytes)."""
    folder_name, files = build_package(repo, solution_id)
    folder = root / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
            zf.writestr(f"{folder_name}/{name}", content)
    for post in repo.posts_df(solution_id).itertuples():
        repo.update_post(post.post_id, asset_directory=str(folder))
    return folder, buffer.getvalue()
