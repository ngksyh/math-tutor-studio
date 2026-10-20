"""Initial courses and starter topics. Safe to run repeatedly (only inserts what is missing).

Course titles are NOT yet verified against current university pages, and no terms are
created: add each term's course page and syllabus under Courses → Terms & links.
"""
from __future__ import annotations

from sqlalchemy import select

from db.models import Course, Topic
from db.repository import Repository

INITIAL_COURSES = [
    dict(course_id="uoft_mat137", university="UofT", course_code="MAT137",
         course_title="Calculus with Proofs", level=100, priority=5),
    dict(course_id="uoft_mat223", university="UofT", course_code="MAT223",
         course_title="Linear Algebra I", level=200, priority=5),
    dict(course_id="ubc_math100", university="UBC", course_code="MATH 100",
         course_title="Differential Calculus with Applications to Physical Sciences and Engineering",
         level=100, priority=4),
    dict(course_id="ubc_math221", university="UBC", course_code="MATH 221",
         course_title="Matrix Algebra", level=200, priority=4),
]

# (name, subject, difficulty, target_level, student_difficulty, course_ids)
STARTER_TOPICS = [
    ("Epsilon–delta definition of a limit", "Calculus", 3, "intro_proofs",
     "Order of quantifiers; choosing delta depending on epsilon", ["uoft_mat137"]),
    ("Mean Value Theorem", "Calculus", 3, "intro_proofs",
     "Checking hypotheses; using MVT to prove inequalities", ["uoft_mat137", "ubc_math100"]),
    ("Related rates", "Calculus", 2, "university_intro",
     "Differentiating before substituting constant values", ["ubc_math100"]),
    ("Linear approximation", "Calculus", 2, "university_intro",
     "Choosing a base point; estimating error", ["ubc_math100", "uoft_mat137"]),
    ("Linear independence and span", "Linear Algebra", 2, "university_intro",
     "Confusing spanning with independence", ["uoft_mat223", "ubc_math221"]),
    ("Eigenvalues and diagonalization", "Linear Algebra", 3, "university_intro",
     "Repeated eigenvalues and when diagonalization fails", ["uoft_mat223", "ubc_math221"]),
    ("Rank–nullity theorem", "Linear Algebra", 3, "university_intro",
     "Applying dimension counting to prove statements", ["uoft_mat223"]),
]


def seed_initial_data(repo: Repository) -> dict[str, int]:
    added = {"courses": 0, "topics": 0}
    with repo.session() as s:
        for data in INITIAL_COURSES:
            if s.get(Course, data["course_id"]) is None:
                s.add(Course(**data))
                added["courses"] += 1
        s.flush()
        existing_names = set(s.scalars(select(Topic.name)))
        for name, subject, diff, level, difficulty_note, course_ids in STARTER_TOPICS:
            if name in existing_names:
                continue
            topic = Topic(name=name, subject=subject, difficulty_level=diff, target_level=level,
                          student_difficulty=difficulty_note)
            topic.courses = [c for cid in course_ids if (c := s.get(Course, cid))]
            s.add(topic)
            added["topics"] += 1
    return added
