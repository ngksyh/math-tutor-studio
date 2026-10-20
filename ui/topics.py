import streamlit as st

from db.models import TARGET_LEVELS
from db.repository import ValidationError
from ui.common import course_label_map, get_repo


def _table_tab(repo, courses: dict[str, str], subjects: list[str]) -> None:
    df = repo.topics_df()
    f1, f2 = st.columns(2)
    course_filter = f1.selectbox("Course", ["All", *courses], format_func=lambda c: courses.get(c, c))
    subject_filter = f2.selectbox("Subject", ["All", *subjects])

    view = df
    if course_filter != "All":
        view = view[view["course_ids"].apply(lambda ids: course_filter in ids)]
        view = view.sort_values(["difficulty_level", "name"])
        st.caption("Sorted by difficulty, which is **relative to the course** (1 = routine exercise for this "
                   "course, 3 = typical exam question, 5 = among the hardest). Check they look consistent.")
    if subject_filter != "All":
        view = view[view["subject"] == subject_filter]

    edited = st.data_editor(
        view.drop(columns=["course_ids"]),
        hide_index=True,
        width="stretch",
        disabled=["topic_id", "courses", "source_count", "last_used_at"],
        column_config={
            "topic_id": None,  # hidden
            "name": st.column_config.TextColumn("Topic", width="medium"),
            "subject": st.column_config.SelectboxColumn("Subject", options=subjects,
                                                        help="New subjects can be added in 'Add topic'."),
            "concept_description": st.column_config.TextColumn("Concept"),
            "difficulty_level": st.column_config.NumberColumn(
                "Difficulty", min_value=1, max_value=5, help="Relative to the course: 1 = routine, 3 = typical exam, 5 = hardest"),
            "target_level": st.column_config.SelectboxColumn("Level", options=TARGET_LEVELS),
            "student_difficulty": st.column_config.TextColumn("Typical difficulty", width="large"),
            "search_query": st.column_config.TextColumn(
                "Search query", help="What to search on Stack Exchange. Empty = use the topic name. "
                                     "Shorter is better, e.g. 'mean value theorem inequality'."),
            "active": st.column_config.CheckboxColumn("Active"),
            "courses": st.column_config.TextColumn("Courses"),
            "source_count": st.column_config.NumberColumn("Sources"),
            "last_used_at": st.column_config.DatetimeColumn("Last used", format="YYYY-MM-DD"),
        },
        key="topic_editor",
    )
    if st.button("Save changes", type="primary"):
        try:
            n = repo.update_topics(edited)
            st.success(f"Saved ({n} field(s) changed).")
        except ValidationError as e:
            st.error(str(e))


def _add_tab(repo, courses: dict[str, str], subjects: list[str]) -> None:
    with st.form("add_topic", clear_on_submit=True):
        name = st.text_input("Topic name *", placeholder="Uniform continuity")
        c1, c2 = st.columns(2)
        subject = c1.selectbox("Subject *", subjects, index=None, accept_new_options=True,
                               placeholder="Choose or type a new subject")
        course_ids = c2.multiselect("Courses", list(courses), format_func=courses.get)
        c3, c4 = st.columns(2)
        difficulty = c3.slider("Difficulty (relative to the course)", 1, 5, 3,
                               help="1 = routine exercise for this course, 3 = typical exam question, 5 = among the hardest")
        level = c4.selectbox("Target level", TARGET_LEVELS)
        concept = st.text_area("Concept description (optional)")
        student_difficulty = st.text_area("What students typically find hard (optional)")
        if st.form_submit_button("Add topic", type="primary"):
            try:
                repo.add_topic(name, subject or "", course_ids, difficulty_level=difficulty,
                               target_level=level, concept_description=concept or None,
                               student_difficulty=student_difficulty or None)
                st.success(f"Added “{name.strip()}”.")
            except ValidationError as e:
                st.error(str(e))


def _links_tab(repo, courses: dict[str, str]) -> None:
    df = repo.topics_df()
    if df.empty:
        st.info("No topics yet.")
        return
    labels = dict(zip(df["topic_id"], df["name"]))
    topic_id = st.selectbox("Topic", list(labels), format_func=labels.get)
    current = df.loc[df["topic_id"] == topic_id, "course_ids"].iloc[0]
    new_ids = st.multiselect("Courses that cover this topic", list(courses), default=current,
                             format_func=courses.get, key=f"tc_{topic_id}")
    if st.button("Save course links"):
        repo.set_topic_courses(topic_id, new_ids)
        st.success("Saved.")


def render() -> None:
    repo = get_repo()
    st.title("Topics")
    st.caption("Mathematical topics that questions will be generated from.")
    courses = course_label_map(repo)
    subjects = repo.subjects()
    tab_table, tab_add, tab_links = st.tabs(["All topics", "Add topic", "Course links"])
    with tab_table:
        _table_tab(repo, courses, subjects)
    with tab_add:
        _add_tab(repo, courses, subjects)
    with tab_links:
        _links_tab(repo, courses)
