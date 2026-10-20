"""Shared helpers for Streamlit pages."""
from __future__ import annotations

import html
import json
import uuid

import streamlit as st

from core.config import database_path, is_demo
from db.repository import Repository
from db.seed import seed_initial_data
from services.demo import load_demo


@st.cache_resource
def get_repo() -> Repository:
    repo = Repository(database_path())
    seed_initial_data(repo)
    if is_demo():
        load_demo(repo)  # only fills an empty demo database
    return repo


_COPY_BUTTON = """
<button id="BTN_ID" type="button" style="font: inherit; font-size: 0.9rem; padding: 0.25rem 0.75rem;
  cursor: pointer; border: 1px solid rgba(128,128,128,0.4); border-radius: 0.5rem; background: transparent;
  color: inherit;">LABEL</button>
<script>
(() => {
  const text = TEXT;
  const btn = document.getElementById("BTN_ID");
  if (!btn) return;
  btn.onclick = async () => {
    try { await navigator.clipboard.writeText(text); }
    catch (e) {  // clipboard API unavailable: fall back to a hidden textarea
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      document.execCommand("copy"); ta.remove();
    }
    btn.textContent = "Copied ✓";
    setTimeout(() => { btn.textContent = "LABEL"; }, 1500);
  };
})();
</script>
"""


def copyable(text: str | None, label: str = "Copy LaTeX") -> None:
    """A single button that copies the raw text (LaTeX included) to the clipboard.

    Rendered with st.html (JavaScript enabled). The text is JSON-encoded, with "</" escaped so it
    can't end the script early; each button gets its own id because st.html isn't iframed."""
    if text:
        js_text = json.dumps(text).replace("</", "<\\/")
        button = (_COPY_BUTTON.replace("BTN_ID", f"copy-{uuid.uuid4().hex[:10]}")
                  .replace("TEXT", js_text).replace("LABEL", html.escape(label)))
        st.html(button, width="content", unsafe_allow_javascript=True)


def text_or_empty(value) -> str:
    """Table cells can hold None or NaN (a float) for missing text — both are truthy-unsafe."""
    return value if isinstance(value, str) else ""


def section(title: str, key: str, *, default_open: bool = True) -> bool:
    """A foldable section heading. Returns True if the section is open (then render its body).

    Unlike st.expander this can contain expanders, and a folded section isn't rendered at all.
    Fold states are saved, so they persist between sessions. Unsaved text typed inside a section
    is discarded when it's folded.
    """
    repo = get_repo()
    if "folded_sections" not in st.session_state:
        st.session_state["folded_sections"] = repo.get_json_setting("folded_sections") or {}
    folded: dict = st.session_state["folded_sections"]
    is_open = not folded.get(key, not default_open)
    if st.button(f"{'▾' if is_open else '▸'} **{title}**", key=f"fold_{key}", type="tertiary"):
        folded[key] = is_open  # toggle: an open section becomes folded
        repo.set_json_setting("folded_sections", folded)
        st.rerun()
    return is_open


def course_label_map(repo: Repository) -> dict[str, str]:
    df = repo.courses_df()
    return {r.course_id: f"{r.university} {r.course_code}" for r in df.itertuples()}


def topic_label_map(repo: Repository) -> dict[str, str]:
    df = repo.topics_df()
    return {r.topic_id: f"{r.name}  ·  {r.subject}" for r in df.itertuples()}
