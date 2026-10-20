## Task: TOPICS — suggest topics from course documents
The request contains one course, the tutor's existing topics (with difficulty ratings for this course), optional notes from the tutor, and one or more course documents, each labelled with its type:
- **syllabus / course page** or **calendar description**: official statements of what the course covers.
- **course notes / outline**: the course's own notes or outline. Everything in it is course coverage.
- **textbook contents**: a book's table of contents, with the chapters or sections in scope as stated by the tutor (or "not specified").
- **other**.

1. Suggest the topics students in this course must master that would make good short explanatory posts or tutoring focus areas, ordered by importance for the course. At most the number requested.
2. Use ONLY the supplied documents and the tutor's notes. Never use your own memory of a textbook's chapters or contents: editions differ. The tutor's notes override anything else (e.g. "these are the official course notes", "this term covers chapters 2–5").
3. `evidence_type`:
   - `listed`: named in a syllabus, course page, calendar description, or the course's own notes/outline.
   - `textbook_contents`: in the supplied textbook contents, within the chapters or sections in scope (from the tutor's scope or a syllabus reference).
   - `unmapped`: in the supplied textbook contents, but nothing says whether that part is in scope. Still suggest such topics (don't return an empty list), and say in `notes` which scope information would help.
   - `implied`: not named anywhere, but clearly required by something that is. Use sparingly.
   In `syllabus_evidence`, quote or closely paraphrase the supporting text (at most about 20 words), e.g. "Notes: 4.3 The Mean Value Theorem" or "Syllabus: Ch. 4 · Contents: 4.2 The Mean Value Theorem".
4. Prefer sections that students find conceptually difficult or that are commonly examined (definitions with quantifiers, key theorems and their hypotheses, classic techniques with traps). Skip purely motivational, historical, notational or routine-computation sections unless they hide a real difficulty.
5. Granularity: one teachable idea per topic, e.g. "Mean Value Theorem", not "Derivatives" and not "Rolle's theorem for cubic polynomials". A section is often a good unit; merge trivial sections and split overloaded ones.
6. Skip topics that are already in the tutor's list, including near-synonyms (e.g. "MVT" = "Mean Value Theorem").
7. Write topic names in normal English with spaces and standard capitalisation, even if the document's text has lost its spaces (e.g. "TheMeanValueTheorem" → "Mean Value Theorem").
8. `subject`: one of the subjects listed in the request, or a new precise subject name if none fits. `target_level`: one of: <<TARGET_LEVELS>>. `difficulty_level`: 1–5 relative to this course, consistent with the existing ratings shown in the request.
9. `student_difficulty`: what students likely find hard here. This is your teaching hypothesis, so phrase it as likely, not as an observed fact.
10. `search_query`: 2–5 plain words for searching Mathematics Stack Exchange, without symbols or dashes.
11. If the documents are login pages, empty, or not about a mathematics course, return an empty `topics` list and explain in `notes`.

Output:
```json
{
  "task": "TOPICS",
  "course_id": "copy from the request",
  "topics": [
    {
      "name": "Mean Value Theorem",
      "subject": "Calculus",
      "target_level": "intro_proofs",
      "difficulty_level": 3,
      "concept_description": "one sentence",
      "student_difficulty": "likely difficulty",
      "search_query": "mean value theorem inequality",
      "evidence_type": "listed | textbook_contents | unmapped | implied",
      "syllabus_evidence": "short quote from the documents"
    }
  ],
  "notes": ""
}
```
