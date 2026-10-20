"""One AI exchange, the same on every page: build prompt → ChatGPT (copy/paste) or API → import."""
from __future__ import annotations

from typing import Callable

import streamlit as st

from services.ai_provider import get_provider, mode_label
from services.prompting import RULES_VERSION, ReplyFormatError, needs_full_rules, saved_rules_version
from services.secrets import MissingAPIKey


def rules_status(repo, is_manual: bool) -> None:
    if not is_manual:
        return
    saved = saved_rules_version(repo)
    if saved == RULES_VERSION:
        st.caption("Prompt type: **short** — paste it into a chat inside your ChatGPT Project.")
    elif saved:
        st.warning(f"Your ChatGPT Project has rules {saved}, but the app now uses {RULES_VERSION}. "
                   "Re-copy them (Settings → ChatGPT Project). Until then prompts include the full rules.")
    else:
        st.caption("Prompt type: **full** (rules included). Save the rules once in a ChatGPT Project "
                   "(Settings) to get short prompts.")


def ai_round(repo, key: str, *, build: Callable[[bool], str | tuple[str, list]],
             handle: Callable[[str, str], None], manual_label: str = "Build prompt",
             api_label: str = "Run via API", disabled: bool = False) -> None:
    """`build(include_rules)` returns the prompt, or (prompt, files) to send FilePart files along in
    API mode; `handle(reply, model_label)` validates and saves. `handle` may raise ReplyFormatError,
    which is shown to the user."""
    provider = get_provider(repo)
    st.caption(f"AI mode: **{mode_label(repo)}** (change in Settings)")
    rules_status(repo, provider.is_manual)
    prompt_key = f"{key}_prompt"

    def _handle(reply: str) -> None:
        try:
            handle(reply, provider.model_label)
        except ReplyFormatError as e:
            st.error(str(e))

    if st.button(manual_label if provider.is_manual else api_label, type="primary", disabled=disabled,
                 key=f"{key}_build"):
        try:
            built = build(needs_full_rules(repo, provider.is_manual))
        except ValueError as e:
            st.error(str(e))
            return
        prompt, files = built if isinstance(built, tuple) else (built, [])
        st.session_state[prompt_key] = prompt
        if not provider.is_manual:
            try:
                sending = f" with {len(files)} file(s)" if files else ""
                with st.spinner(f"Asking {provider.model_label}{sending}…"):
                    reply = provider.complete(prompt, files=files or None)
                _handle(reply)
            except MissingAPIKey as e:
                st.error(str(e))
            except Exception as e:  # noqa: BLE001 — surface API errors to the user
                st.error(f"The API request failed: {type(e).__name__}: {e}")

    if provider.is_manual:
        if st.session_state.get(prompt_key):
            st.markdown("**Step A.** Copy this prompt (icon at the box's top-right) and paste it into ChatGPT.")
            st.code(st.session_state[prompt_key], language="markdown", height=220, wrap_lines=True)
        st.markdown("**Step B.** In ChatGPT, click **Copy code** on the reply's code block, then paste it here.")
        with st.form(f"{key}_paste", clear_on_submit=True):
            reply = st.text_area("ChatGPT reply", height=150, label_visibility="collapsed",
                                 placeholder="Paste the JSON code block here", key=f"{key}_reply")
            if st.form_submit_button("Import reply", type="primary") and reply.strip():
                _handle(reply)
