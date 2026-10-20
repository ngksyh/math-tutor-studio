import streamlit as st

from core.config import load_config
from db.models import SELECTION_STATUSES
from db.repository import ValidationError
from services.latex import has_unwrapped_math, normalize_math
from services.question_generator import (
    PROMPT_VERSION,
    ValidationReport,
    build_prompt,
    current_preferences,
    new_batch_id,
    plaintext,
    save_accepted,
    validate_reply,
)
from services.topic_ranker import SIGNAL_LABELS, SIGNALS, current_weights, rank_topics
from ui.ai_round import ai_round
from ui.common import copyable, get_repo, section, text_or_empty

STATUS_ICONS = {"candidate": "○", "favorite": "★", "selected": "✓", "rejected": "✗"}


def _choose_topics(repo) -> list[str]:
    ranked = rank_topics(repo)
    n_default = int(load_config()["ai"].get("questions_per_batch", 5))
    ranked.insert(0, "use", ranked.index < n_default)
    st.session_state["ranking_snapshot"] = ranked
    if not section("1 · Choose topics", "q_topics"):
        st.caption(f"Folded — using the top {n_default} ranked topics.")
        return list(ranked.loc[ranked["use"], "topic_id"])
    if ranked.empty:
        st.info("No active topics. Add some on the Topics page.")
        return []
    st.caption("Ranked by the score in Settings. ⚠ = no evidence yet (a neutral prior is used, not zero). "
               "Tick the topics to generate from.")
    edited = st.data_editor(
        ranked.assign(flag=ranked["evidence_missing"].map({True: "⚠", False: ""})),
        hide_index=True,
        width="stretch",
        disabled=[c for c in ranked.columns if c != "use"] + ["flag"],
        column_order=["use", "final_score", "flag", "topic", "subject", "courses", "source_count", *SIGNALS,
                      "notes"],
        column_config={
            "use": st.column_config.CheckboxColumn("Use"),
            "final_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
            "flag": st.column_config.TextColumn("", width="small"),
            "topic": st.column_config.TextColumn("Topic", width="medium"),
            "source_count": st.column_config.NumberColumn("Sources"),
            **{s: st.column_config.NumberColumn(SIGNAL_LABELS[s], format="%.2f") for s in SIGNALS},
            "notes": st.column_config.TextColumn("Notes", width="large"),
        },
        key="rank_editor",
    )
    return list(edited.loc[edited["use"], "topic_id"])


def _show_report(report: ValidationReport) -> None:
    for w in report.warnings:
        st.warning(w)
    if report.accepted:
        st.success(f"Imported {len(report.accepted)} question(s) as unverified candidates.")
    if report.rejected:
        with st.expander(f"{len(report.rejected)} rejected", expanded=not report.accepted):
            for r in report.rejected:
                st.markdown(f"**#{r.index} {r.title}**")
                for reason in r.reasons:
                    st.markdown(f"- {reason}")


def _import(repo, reply: str, batch_id: str | None, model_label: str) -> None:
    report = validate_reply(repo, reply, expected_batch_id=batch_id)  # ReplyFormatError shown by ai_round
    if report.accepted:
        save_accepted(repo, report, batch_id=batch_id or "batch_unknown", generation_model=model_label)
    st.session_state["last_report"] = report


def _preferences(repo) -> None:
    with st.expander("Your preferences (sent with every request)"):
        with st.form("preferences"):
            prefs = st.text_area("Preferences", value=current_preferences(repo), height=110,
                                 label_visibility="collapsed")
            if st.form_submit_button("Save preferences"):
                repo.set_setting("question_preferences", prefs)
                st.success("Saved — applies from the next prompt.")


def _generate(repo, topic_ids: list[str]) -> None:
    if not section("2 · Generate questions", "q_generate"):
        return
    _preferences(repo)
    n = st.number_input("Number of questions", 1, 10, int(load_config()["ai"].get("questions_per_batch", 5)))
    if not topic_ids:
        st.caption("Tick at least one topic above.")

    def build(include_rules: bool) -> str:
        batch_id = new_batch_id()
        prompt = build_prompt(repo, topic_ids, int(n), batch_id, include_rules=include_rules,
                              preferences=current_preferences(repo))
        snapshot = st.session_state["ranking_snapshot"]
        repo.save_topic_scores(snapshot[snapshot["topic_id"].isin(topic_ids)], batch_id=batch_id,
                               version=load_config()["ranking"]["version"])
        st.session_state.update(batch_id=batch_id, last_report=None)
        return prompt

    ai_round(repo, "questions", build=build, api_label="Generate questions via API", disabled=not topic_ids,
             handle=lambda reply, model: _import(repo, reply, st.session_state.get("batch_id"), model))
    if st.session_state.get("last_report"):
        _show_report(st.session_state["last_report"])


def _edit_form(repo, q, expanded: bool) -> None:
    with st.expander("Edit text", expanded=expanded):
        st.caption("Use `$...$` for inline maths and `$$...$$` for display maths.")
        with st.form(f"edit_{q.question_id}"):
            title = st.text_input("Title", value=q.title or "")
            latex = st.text_area("Question", value=q.question_latex, height=140)
            insight = st.text_area("Teaching insight", value=q.teaching_insight or "", height=80)
            solution = st.text_area("AI-proposed solution", value=q.proposed_solution or "", height=160)
            c1, c2 = st.columns(2)
            preview = c1.form_submit_button("Preview")
            save = c2.form_submit_button("Save", type="primary")
        if preview:
            with st.container(border=True):
                st.markdown(normalize_math(latex))
        if save:
            try:
                latex = normalize_math(latex)
                repo.update_question_text(q.question_id, title=title.strip(), question_latex=latex,
                                          question_plaintext=plaintext(latex),
                                          teaching_insight=normalize_math(insight),
                                          proposed_solution=normalize_math(solution))
                st.rerun()
            except ValidationError as e:
                st.error(str(e))


def _question_card(repo, q, topics_sources: dict) -> None:
    with st.container(border=True):
        head, meta = st.columns([3, 2])
        head.markdown(f"**{STATUS_ICONS[q.selection_status]} {q.title or 'Untitled'}**")
        meta.caption(f"{q.topic} · {q.question_type} · difficulty {q.difficulty}/5 · "
                     f"~{q.expected_time_min or '?'} min · {q.target_audience}")
        st.markdown(normalize_math(q.question_latex))
        if q.teaching_insight:
            st.markdown(f"*Teaching insight:* {normalize_math(q.teaching_insight)}")
        with st.expander("AI-proposed solution — unverified, check before using"):
            st.markdown(normalize_math(q.proposed_solution) or "_none_")
            copyable(q.proposed_solution)
        broken = [name for name, text in (("question", q.question_latex), ("solution", q.proposed_solution),
                                          ("insight", q.teaching_insight)) if has_unwrapped_math(text)]
        if broken:
            st.warning(f"Some maths in the {', '.join(broken)} is missing its dollar-sign delimiters and "
                       "shows as raw code. Fix it under **Edit text**.", icon="⚠️")
        _edit_form(repo, q, expanded=False)
        if q.source_ids:
            links = []
            for sid in q.source_ids:
                title, url = topics_sources.get(sid, (sid, None))
                links.append(f"[{title}]({url})" if text_or_empty(url) else title)
            st.caption("Evidence: " + " · ".join(links))
        else:
            st.caption("Evidence: none (based on teaching judgment)")
        b = st.columns(5)
        if b[0].button("★ Favorite", key=f"fav_{q.question_id}"):
            repo.set_question_status(q.question_id, selection_status="favorite")
            st.rerun()
        if b[1].button("✓ Select", key=f"sel_{q.question_id}", help="Use this one for a post"):
            repo.set_question_status(q.question_id, selection_status="selected")
            st.rerun()
        if b[2].button("✗ Reject", key=f"rej_{q.question_id}"):
            repo.set_question_status(q.question_id, selection_status="rejected")
            st.rerun()
        if b[3].button("↺ Reset", key=f"res_{q.question_id}"):
            repo.set_question_status(q.question_id, selection_status="candidate")
            st.rerun()
        if b[4].button("Delete", key=f"del_{q.question_id}"):
            repo.delete_question(q.question_id)
            st.rerun()


def _review(repo) -> None:
    if not section("3 · Review candidates", "q_review"):
        return
    statuses = st.pills("Show", SELECTION_STATUSES, selection_mode="multi",
                        default=["candidate", "favorite", "selected"])
    df = repo.questions_df(statuses or SELECTION_STATUSES)
    if df.empty:
        st.info("No questions here yet.")
        return
    sources = repo.sources_df()
    lookup = {r.source_id: (r.title, r.url) for r in sources.itertuples()}
    for q in df.itertuples():
        _question_card(repo, q, lookup)


def render() -> None:
    repo = get_repo()
    st.title("Question Studio")
    st.caption("Pick topics → generate original candidates → keep the best. "
               f"All AI solutions are marked unverified. Prompt version: {PROMPT_VERSION}. "
               f"Score weights: {', '.join(f'{SIGNAL_LABELS[k]} {v:.2f}' for k, v in current_weights(repo).items())}")
    topic_ids = _choose_topics(repo)
    st.divider()
    _generate(repo, topic_ids)
    st.divider()
    _review(repo)
