import streamlit as st

from db.models import TERM_SEASONS
from db.repository import ValidationError
from ui.common import get_repo
from ui.forms import add_course_form, add_offering_form


def _courses_tab(repo) -> None:
    st.caption("A course is what stays the same each year. Edit cells, then **Save changes**. "
               "Priority 5 = most important; untick *Monitor* to pause a course.")
    df = repo.courses_df()
    edited = st.data_editor(
        df,
        hide_index=True,
        width="stretch",
        disabled=["course_id", "offerings"],
        column_config={
            "course_id": st.column_config.TextColumn("ID"),
            "university": st.column_config.TextColumn("University"),
            "course_code": st.column_config.TextColumn("Code"),
            "course_title": st.column_config.TextColumn("Title", width="large"),
            "level": st.column_config.NumberColumn("Level", min_value=0, max_value=9999, step=1),
            "priority": st.column_config.NumberColumn("Priority", min_value=1, max_value=5, step=1),
            "active": st.column_config.CheckboxColumn("Monitor"),
            "calendar_url": st.column_config.LinkColumn("Calendar page"),
            "offerings": st.column_config.NumberColumn("Terms"),
        },
        key="course_editor",
    )
    if st.button("Save changes", type="primary", key="save_courses"):
        try:
            n = repo.update_courses(edited)
            st.success(f"Saved ({n} field(s) changed).")
        except ValidationError as e:
            st.error(str(e))

    with st.expander("Delete a course"):
        labels = {r.course_id: f"{r.university} {r.course_code}" for r in df.itertuples()}
        if labels:
            cid = st.selectbox("Course", list(labels), format_func=labels.get, key="del_course")
            st.caption("Removes the course and its terms. Sources and topics are kept but unlinked. "
                       "To just stop tracking it, untick *Monitor* instead.")
            if st.checkbox(f"Yes, delete {labels[cid]}", key="del_course_confirm"):
                if st.button("Delete course"):
                    repo.delete_course(cid)
                    st.rerun()


def _terms_tab(repo) -> None:
    st.caption("Each term has its own pages, instructor and exam dates. "
               "Tick *Monitor* on the current terms you want the collector to re-check.")
    courses = repo.courses_df()
    labels = {r.course_id: f"{r.university} {r.course_code}" for r in courses.itertuples()}
    course_filter = st.selectbox("Course", ["All", *labels], format_func=lambda c: labels.get(c, c))
    df = repo.offerings_df(None if course_filter == "All" else course_filter)
    if df.empty:
        st.info("No terms yet. Add one below.")
    else:
        edited = st.data_editor(
            df.drop(columns=["course_id", "term"]),
            hide_index=True,
            width="stretch",
            disabled=["offering_id", "course"],
            column_config={
                "offering_id": None,
                "course": st.column_config.TextColumn("Course"),
                "term_year": st.column_config.NumberColumn("Year", min_value=1950, max_value=2100,
                                                           step=1, format="%d"),
                "term_season": st.column_config.SelectboxColumn("Term", options=TERM_SEASONS),
                "section": st.column_config.TextColumn("Section"),
                "instructor": st.column_config.TextColumn("Instructor"),
                "course_url": st.column_config.LinkColumn("Course site"),
                "syllabus_url": st.column_config.LinkColumn("Syllabus"),
                "exam_notes": st.column_config.TextColumn("Exam dates"),
                "monitor": st.column_config.CheckboxColumn("Monitor"),
                "last_verified_at": st.column_config.DatetimeColumn("Last verified", format="YYYY-MM-DD"),
                "notes": st.column_config.TextColumn("Notes"),
            },
            key="offering_editor",
        )
        b1, b2 = st.columns([1, 4])
        if b1.button("Save changes", type="primary", key="save_offerings"):
            try:
                n = repo.update_offerings(edited)
                st.success(f"Saved ({n} field(s) changed).")
            except ValidationError as e:
                st.error(str(e))
        with b2.popover("Delete a term"):
            term_labels = dict(zip(df["offering_id"], df["course"] + " " + df["term"]))
            oid = st.selectbox("Term", list(term_labels), format_func=term_labels.get)
            if st.button("Delete term"):
                repo.delete_offering(oid)
                st.rerun()

    st.subheader("Add a term")
    add_offering_form(repo, key="courses_page")


def render() -> None:
    repo = get_repo()
    st.title("Courses")
    tab_courses, tab_terms, tab_add = st.tabs(["Courses", "Terms & links", "Add course"])
    with tab_courses:
        _courses_tab(repo)
    with tab_terms:
        _terms_tab(repo)
    with tab_add:
        add_course_form(repo, key="courses_page")
