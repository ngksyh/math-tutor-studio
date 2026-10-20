from datetime import datetime

import streamlit as st

from db.repository import ValidationError
from services.latex import has_unwrapped_math, normalize_math
from services.math_reviewer import build_review_prompt, load_review, parse_review, save_review
from ui.ai_round import ai_round
from ui.common import copyable, get_repo, section

FIELDS = {
    "raw_reasoning": ("Solution *", 300,
                      "Your own reasoning, or the AI solution after you've checked and edited it. "
                      "AI never overwrites what's saved here."),
    "intuition": ("Key intuition (optional)", 90, "The one idea a student should remember."),
    "common_mistakes": ("Common mistakes (optional)", 90, "What students typically get wrong here."),
    "alternate_methods": ("Alternative methods (optional)", 90, ""),
}
VERDICT_STYLE = {"no_issues_found": ("success", "No issues found"), "minor_issues": ("warning", "Minor issues"),
                 "major_issues": ("error", "Major issues"), "unknown": ("info", "Review")}


def _pick_question(repo) -> str | None:
    questions = repo.questions_df(["selected", "favorite"])
    with_solution = set(repo.solutions_df()["question_id"])
    if questions.empty:
        st.info("No selected questions yet. In **Question Studio**, press ✓ Select on a question you want to post.")
        return None
    questions = questions.assign(order=~questions["question_id"].isin(with_solution))

    def label(row) -> str:
        mark = {"verified": "✅", "flagged": "⚠️", "ai_checked": "🔎"}.get(row.review_status, "✏️")
        return f"{mark} {row.title or 'Untitled'} · {row.topic} ({row.selection_status})"

    labels = {r.question_id: label(r) for r in questions.sort_values("order").itertuples()}
    st.caption("✏️ not yet verified · 🔎 AI-checked · ⚠️ AI found major issues · ✅ approved by you")
    return st.selectbox("Question", list(labels), format_func=labels.get)


def _editor(repo, question: dict, solution: dict | None) -> None:
    qid = question["question_id"]
    keys = {f: f"ws_{f}_{qid}" for f in FIELDS}
    restore = st.session_state.pop(f"ws_restore_{qid}", None)  # set by "Restore this version"
    for f, k in keys.items():  # first visit: load what's saved
        if restore is not None:
            st.session_state[k] = restore.get(f) or ""
        st.session_state.setdefault(k, (solution or {}).get(f) or "")
    if restore is not None:
        st.info("An earlier version is loaded in the editor — press **Save solution** to keep it.")
    if flash := st.session_state.pop("ws_flash", None):
        st.success(flash)

    if solution is None and (question.get("proposed_solution") or "").strip():
        c1, c2 = st.columns([1, 2])
        if c1.button("Start from the AI solution", key=f"from_ai_{qid}"):
            try:
                repo.start_from_ai(qid)
                for k in keys.values():
                    st.session_state.pop(k, None)  # reload the editor from the new solution
                st.session_state["ws_flash"] = "AI solution loaded. Check every step, edit what's needed, then save."
                st.rerun()
            except ValidationError as e:
                st.error(str(e))
        c2.caption("…or write your own below. Either way, nothing is published until you approve it.")
    elif solution and solution.get("origin") == "ai_verified":
        st.caption("🤖 Started from the AI solution — you're the checker. Approval below is still required.")

    left, right = st.columns(2)
    with left:
        st.markdown("**Write**")
        for f, (label, height, help_) in FIELDS.items():
            st.text_area(label, key=keys[f], height=height, help=help_ or None)
        st.caption("Maths: `$...$` inline, `$$...$$` display. The preview updates when you click outside a box.")
        if st.button("Save solution", type="primary"):
            try:
                sid, changed = repo.save_solution(qid, **{f: st.session_state[k] for f, k in keys.items()})
                if not changed:
                    st.info("No changes to save.")
                else:
                    st.session_state["ws_flash"] = "Saved." + (
                        " Approval was withdrawn because the mathematics changed."
                        if solution and solution["approved"] else "")
                    st.rerun()
            except ValidationError as e:
                st.error(str(e))
    with right:
        st.markdown("**Preview**")
        with st.container(border=True):
            for f, (label, _, _) in FIELDS.items():
                text = st.session_state[keys[f]]
                if text.strip():
                    if f != "raw_reasoning":
                        st.markdown(f"**{label.rstrip(' *').replace(' (optional)', '')}**")
                    st.markdown(normalize_math(text))
                    if has_unwrapped_math(text):
                        st.caption("⚠️ Some maths here is missing dollar-sign delimiters.")

    if solution:
        revisions = repo.revisions_df(solution["solution_id"])
        with st.expander(f"Revision history ({len(revisions)})"):
            for i, rev in enumerate(revisions.itertuples()):
                who = "AI draft" if rev.author == "ai" else "you"
                st.markdown(f"**{rev.created_at:%Y-%m-%d %H:%M}** · {who}" + (" (current)" if i == 0 else ""))
                st.text(rev.raw_reasoning[:400] + ("…" if len(rev.raw_reasoning) > 400 else ""))
                if i > 0 and st.button("Restore this version", key=f"restore_{rev.revision_id}"):
                    st.session_state[f"ws_restore_{qid}"] = {f: getattr(rev, f) for f in FIELDS}
                    st.rerun()


def _review(repo, question: dict, solution: dict) -> None:
    if not section("2 · AI math check (optional, recommended)", "ws_review"):
        return
    st.caption("ChatGPT reads your solution and lists possible issues. It never edits your text, "
               "and it can be wrong — you decide.")
    review = load_review(solution.get("math_review_notes"))
    if review:
        kind, label = VERDICT_STYLE.get(review.get("verdict"), VERDICT_STYLE["unknown"])
        getattr(st, kind)(f"**{label}** — {review.get('summary', '')}")
        reviewed_at = review.get("reviewed_at")
        if reviewed_at and solution["updated_at"] and \
                datetime.fromisoformat(reviewed_at).replace(tzinfo=None) < solution["updated_at"].replace(tzinfo=None):
            st.caption("ℹ️ You've edited the solution since this check.")
        for issue in review.get("issues", []):
            icon = {"major": "🔴", "minor": "🟡", "style": "⚪"}.get(issue["severity"], "•")
            with st.container(border=True):
                st.markdown(f"{icon} **{issue['severity']}** — *{issue.get('location') or 'general'}*")
                st.markdown(issue["description"])
                if issue.get("suggestion"):
                    st.markdown(f"**Suggestion:** {issue['suggestion']}")

    def handle(reply: str, model: str) -> None:
        result = parse_review(reply, solution["solution_id"])
        for w in result.warnings:
            st.warning(w)
        save_review(repo, solution["solution_id"], result, model)
        st.rerun()

    ai_round(repo, "review", manual_label="Build review prompt", api_label="Check via API",
             build=lambda full: build_review_prompt(repo, question["question_id"], include_rules=full),
             handle=handle)


def _approve(repo, solution: dict) -> None:
    if not section("3 · Your approval", "ws_approve"):
        return
    review = load_review(solution.get("math_review_notes")) or {}
    if review.get("verdict") == "major_issues" and not solution["approved"]:
        st.warning("The AI check reported major issues. Approve only if you've confirmed they're not real.")
    approved = st.checkbox("I have checked every step of this mathematics myself and it's ready to publish",
                           value=bool(solution["approved"]), key=f"approve_{solution['solution_id']}")
    if approved != bool(solution["approved"]):
        repo.set_solution_approved(solution["solution_id"], approved)
        st.rerun()
    if solution["approved"]:
        st.success("Approved. Next: **Publishing Studio** to create the Xiaohongshu / Instagram drafts.")


def render() -> None:
    repo = get_repo()
    st.title("Reasoning Workspace")
    st.caption("Write your own solution, or start from the AI's and verify it. Either way, you approve "
               "the mathematics before anything is published.")
    qid = _pick_question(repo)
    if not qid:
        return
    question, solution = repo.get_question(qid), repo.get_solution(qid)

    with st.container(border=True):
        st.markdown(f"**{question['title']}** · {question['topic']} · difficulty {question['difficulty']}/5")
        st.markdown(normalize_math(question["question_latex"]))
        with st.expander("AI-proposed solution (unverified — for reference only)"):
            st.markdown(normalize_math(question["proposed_solution"] or "_none_"))
            copyable(question["proposed_solution"])
            if question["teaching_insight"]:
                st.markdown(f"*Teaching insight:* {normalize_math(question['teaching_insight'])}")

    if section("1 · Solution", "ws_solution"):
        _editor(repo, question, solution)
    if solution:
        st.divider()
        _review(repo, question, solution)
        st.divider()
        _approve(repo, solution)
