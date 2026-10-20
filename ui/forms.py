"""Reusable forms (shown on more than one page)."""
from __future__ import annotations

from datetime import date

import streamlit as st

from db.models import TERM_SEASONS
from db.repository import Repository, ValidationError

MIN_DATE = date(1990, 1, 1)


def add_course_form(repo: Repository, key: str) -> None:
    with st.form(f"{key}_add_course", clear_on_submit=True):
        c1, c2 = st.columns(2)
        university = c1.text_input("University *", placeholder="UofT")
        code = c2.text_input("Course code *", placeholder="MAT464")
        title = st.text_input("Course title *", placeholder="Differential Geometry")
        c3, c4 = st.columns(2)
        level = c3.number_input("Level", min_value=0, max_value=9999, value=100, step=100,
                                help="e.g. 100 for first-year, 400 for fourth-year, 1000+ for graduate")
        priority = c4.slider("Priority", 1, 5, 3)
        url = st.text_input("Official calendar / course description URL (optional)",
                            help="The stable page describing the course. Term-specific pages go under Terms.")
        if st.form_submit_button("Add course", type="primary"):
            try:
                cid = repo.add_course(university, code, title, int(level), priority, url)
                st.success(f"Added {cid}. You can now add its terms.")
            except ValidationError as e:
                st.error(str(e))


def add_offering_form(repo: Repository, key: str) -> None:
    courses = repo.courses_df()
    if courses.empty:
        st.info("Add a course first.")
        return
    labels = {r.course_id: f"{r.university} {r.course_code} — {r.course_title}" for r in courses.itertuples()}
    this_year = date.today().year
    with st.form(f"{key}_add_offering", clear_on_submit=True):
        course_id = st.selectbox("Course *", list(labels), format_func=labels.get)
        c1, c2, c3 = st.columns(3)
        year = c1.number_input("Year *", min_value=1950, max_value=2100, value=this_year, step=1)
        season = c2.selectbox("Term *", TERM_SEASONS, index=TERM_SEASONS.index("Fall"))
        section = c3.text_input("Section (optional)", placeholder="LEC0101",
                                help="Leave blank if the page covers all sections.")
        instructor = st.text_input("Instructor (optional)")
        course_url = st.text_input("Course website for this term (optional)")
        syllabus_url = st.text_input("Syllabus URL for this term (optional)")
        exam_notes = st.text_input("Midterm / exam dates (optional)", placeholder="Midterm Oct 21; final in Dec")
        monitor = st.checkbox("Monitor these pages for changes", value=year >= this_year,
                              help="Only monitored terms will be re-checked by the research collector.")
        if st.form_submit_button("Add term", type="primary"):
            try:
                repo.add_offering(course_id, int(year), season, section=section, instructor=instructor,
                                  course_url=course_url, syllabus_url=syllabus_url,
                                  exam_notes=exam_notes, monitor=monitor)
                st.success(f"Added {labels[course_id].split(' — ')[0]} {year} {season}.")
            except ValidationError as e:
                st.error(str(e))
