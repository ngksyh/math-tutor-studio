"""Previous / next links between the workflow pages."""
from __future__ import annotations

from typing import Callable

import streamlit as st

# Workflow order: the pages the previous/next links move between.
WORKFLOW = ["dashboard", "research", "questions", "workspace", "publishing"]
PAGES: dict[str, "st.Page"] = {}  # filled by app.py on every run


def nav_bar(key: str) -> None:
    if key not in WORKFLOW or not PAGES:  # PAGES is empty when a page is run on its own (tests)
        return
    i = WORKFLOW.index(key)
    prev_key = WORKFLOW[i - 1] if i > 0 else None
    next_key = WORKFLOW[i + 1] if i < len(WORKFLOW) - 1 else None
    left, _, right = st.columns([3, 1, 3])
    if prev_key:
        left.page_link(PAGES[prev_key], label=f"← {PAGES[prev_key].title}")
    if next_key:
        right.page_link(PAGES[next_key], label=f"{PAGES[next_key].title} →")


def with_nav(key: str, render: Callable[[], None]) -> Callable[[], None]:
    """Wrap a page so the links appear at the top and the bottom (even if the page returns early)."""
    def page() -> None:
        nav_bar(key)
        render()
        st.divider()
        nav_bar(key)

    page.__name__ = f"page_{key}"
    return page
