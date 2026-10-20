"""A brief overview of every stored question."""
import altair as alt
import pandas as pd
import streamlit as st

from db.models import SELECTION_STATUSES
from ui.common import get_repo

BAR = "#2a78d6"  # single series → one hue, no legend needed


def _bar(chart: alt.Chart, thickness: int | None = None) -> alt.Chart:
    mark = {"size": thickness} if thickness else {}
    return (chart.mark_bar(color=BAR, cornerRadiusEnd=4, **mark)
            .configure_axis(gridOpacity=0.35, domain=False, labelOverlap=False))


def render() -> None:
    repo = get_repo()
    st.title("Question Library")
    questions = repo.questions_df()
    if questions.empty:
        st.info("No questions stored yet — generate some in **Question Studio**.")
        return

    solutions = repo.solutions_df()
    posts = repo.posts_df()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Questions stored", len(questions))
    c2.metric("Selected for posts", int((questions["selection_status"] == "selected").sum()))
    c3.metric("Solutions approved", int(solutions["approved"].sum()) if not solutions.empty else 0)
    c4.metric("Posts published", int((posts["status"] == "published").sum()) if not posts.empty else 0)

    include_rejected = st.toggle("Include rejected questions", value=False)
    view = questions if include_rejected else questions[questions["selection_status"] != "rejected"]
    if view.empty:
        st.info("Nothing to show with this filter.")
        return

    # --- per topic: total bar; the status breakdown lives in the tooltip
    counts = pd.crosstab(view["topic"].fillna("(deleted topic)"), view["selection_status"])
    counts = counts.reindex(columns=SELECTION_STATUSES, fill_value=0)
    counts["total"] = counts.sum(axis=1)
    counts.columns.name = None
    by_topic = counts.rename_axis("topic").reset_index()

    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Questions per topic**")
        chart = alt.Chart(by_topic).encode(
            y=alt.Y("topic:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260, labelOverlap=False)),
            x=alt.X("total:Q", title="Questions", axis=alt.Axis(tickMinStep=1)),
            tooltip=[alt.Tooltip("topic:N", title="Topic"), alt.Tooltip("total:Q", title="Total"),
                     *[alt.Tooltip(f"{s}:Q", title=s.capitalize()) for s in SELECTION_STATUSES
                       if include_rejected or s != "rejected"]],
        ).properties(height=max(200, 40 * len(by_topic)))
        st.altair_chart(_bar(chart, 18), width="stretch")

    # --- difficulty 1–5 (all levels shown, even empty ones)
    by_level = (view["difficulty"].value_counts().reindex(range(1, 6), fill_value=0)
                .rename_axis("difficulty").reset_index(name="questions"))
    with right:
        st.markdown("**Difficulty**")
        chart = alt.Chart(by_level).encode(
            x=alt.X("difficulty:O", title="1 = routine · 5 = hard", axis=alt.Axis(labelAngle=0),
                    scale=alt.Scale(paddingInner=0.35)),
            y=alt.Y("questions:Q", title="Questions", axis=alt.Axis(tickMinStep=1)),
            tooltip=[alt.Tooltip("difficulty:O", title="Difficulty"), alt.Tooltip("questions:Q", title="Questions")],
        ).properties(height=200)
        st.altair_chart(_bar(chart), width="stretch")

    with st.expander("Table view"):
        st.dataframe(by_topic, hide_index=True, width="stretch")
        st.dataframe(view[["title", "topic", "question_type", "difficulty", "selection_status", "review_status",
                           "created_at"]], hide_index=True, width="stretch",
                     column_config={"created_at": st.column_config.DatetimeColumn("Created", format="YYYY-MM-DD")})
