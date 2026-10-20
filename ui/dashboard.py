import streamlit as st

from services.ai_provider import PROVIDERS, current_mode, current_provider, mode_label
from services.secrets import get_api_key, mask_key
from ui.common import get_repo, section


def render() -> None:
    repo = get_repo()
    st.title("Math Tutor Studio")
    st.caption("source → topic → problem → your reasoning → bilingual post → measured result")

    counts = repo.table_counts()
    courses = repo.courses_df()
    topics = repo.topics_df()
    sources = repo.sources_df(include_excluded=False)

    questions = repo.questions_df()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active courses", int(courses["active"].sum()) if not courses.empty else 0)
    c2.metric("Topics", counts["topics"])
    c3.metric("Sources", len(sources))
    open_q = int(questions["selection_status"].isin(["candidate", "favorite"]).sum())
    selected = int((questions["selection_status"] == "selected").sum())
    c4.metric("Open questions", open_q, help=f"{selected} selected for posts")

    solutions = repo.solutions_df()
    posts = repo.posts_df()
    approved = int(solutions["approved"].sum()) if not solutions.empty else 0
    published = int((posts["status"] == "published").sum()) if not posts.empty else 0
    st.markdown(f"**Pipeline:** {selected} selected question(s) → {len(solutions)} solution(s) written, "
                f"{approved} approved → {len(posts)} post draft(s), {published} published")

    if current_mode(repo) == "api":
        info = PROVIDERS[current_provider(repo)]
        key = get_api_key(key_name=info.key_name)
        if key:
            st.success(f"AI mode: {mode_label(repo)} · key {mask_key(key)}")
        else:
            st.warning(f"AI mode is the {info.label} API, but no key is saved — add one in **Settings**.")
    else:
        st.info(f"AI mode: {mode_label(repo)}.")

    runs = repo.runs_df(limit=1)
    if runs.empty:
        st.caption("No automatic research yet — try **Research → Automatic (Stack Exchange)**.")
    else:
        last = runs.iloc[0]
        st.caption(f"Last research run: {last['started_at']:%Y-%m-%d %H:%M} · {last['status']} · "
                   f"{last['sources_added']} new source(s)")

    left, right = st.columns(2)
    with left:
        if not section("Recent sources", "dash_sources"):
            pass
        elif sources.empty:
            st.info("No sources yet. Add one on the **Research** page.")
        else:
            st.dataframe(
                sources[["title", "source_type", "course_id", "topics", "fetched_at"]].head(10),
                hide_index=True, width="stretch",
                column_config={"fetched_at": st.column_config.DatetimeColumn("Added", format="YYYY-MM-DD")},
            )
    with right:
        if not section("Topics needing evidence", "dash_topics"):
            pass
        elif topics.empty:
            st.info("No topics yet.")
        else:
            need = topics[(topics["active"]) & (topics["source_count"] == 0)]
            st.dataframe(need[["name", "subject", "courses"]],
                         hide_index=True, width="stretch")
            st.caption("Linking sources to these topics will make next phase's ranking meaningful.")

    offerings = repo.offerings_df()
    monitored = set(offerings.loc[offerings["monitor"].astype(bool), "course_id"]) if not offerings.empty else set()
    missing = courses[courses["active"] & ~courses["course_id"].isin(monitored)] if not courses.empty else courses
    if not missing.empty:
        names = ", ".join(f"{r.university} {r.course_code}" for r in missing.itertuples())
        st.info(f"No monitored current term for: {names}. Add this term's course page and syllabus under "
                "**Courses → Terms & links** so the collector knows where to look.")
