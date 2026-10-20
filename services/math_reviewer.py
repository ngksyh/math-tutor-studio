"""AI check of YOUR solution. It only reports issues — it never edits your reasoning,
and its verdict never replaces your own approval."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from db.models import utcnow
from db.repository import Repository
from services.latex import normalize_math
from services.prompting import ReplyFormatError, build_request, check_task, parse_json_reply

VERDICTS = ["no_issues_found", "minor_issues", "major_issues"]


class Issue(BaseModel):
    severity: Literal["major", "minor", "style"]
    location: str = ""
    description: str = Field(min_length=1)
    suggestion: str = ""


class ReviewResult(BaseModel):
    verdict: Literal["no_issues_found", "minor_issues", "major_issues"]
    summary: str = ""
    issues: list[Issue] = Field(default_factory=list)


@dataclass
class ReviewImport:
    result: ReviewResult
    warnings: list[str]


def _solution_block(question: dict, solution: dict) -> str:
    parts = [f"## Problem\n{question['question_latex']}", f"## Tutor's solution\n{solution['raw_reasoning']}"]
    for key, label in (("intuition", "Key intuition"), ("common_mistakes", "Common mistakes"),
                       ("alternate_methods", "Alternative methods")):
        if solution.get(key):
            parts.append(f"## {label}\n{solution[key]}")
    return "\n\n".join(parts)


def build_review_prompt(repo: Repository, question_id: str, *, include_rules: bool) -> str:
    question, solution = repo.get_question(question_id), repo.get_solution(question_id)
    if not question or not solution:
        raise ValueError("Save your solution first.")
    body = f"solution_id: {solution['solution_id']}\n\n{_solution_block(question, solution)}"
    return build_request("MATH_REVIEW", body, include_rules=include_rules)


def parse_review(reply: str, expected_solution_id: str) -> ReviewImport:
    data = parse_json_reply(reply)
    if not isinstance(data, dict):
        raise ReplyFormatError("Expected a JSON object with verdict, summary and issues.")
    check_task(data, "MATH_REVIEW")
    try:
        result = ReviewResult.model_validate(data)
    except ValidationError as e:
        details = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        raise ReplyFormatError(f"The review doesn't match the expected format ({details}).") from e
    warnings = []
    if data.get("solution_id") and data["solution_id"] != expected_solution_id:
        warnings.append("This review mentions a different solution_id — make sure you pasted the right reply.")
    if result.verdict == "no_issues_found" and result.issues:
        warnings.append("The verdict says no issues, but issues were listed. Read them before approving.")
    if any(i.severity == "major" for i in result.issues) and result.verdict != "major_issues":
        result.verdict = "major_issues"
    for issue in result.issues:
        issue.description, issue.suggestion = normalize_math(issue.description), normalize_math(issue.suggestion)
    return ReviewImport(result, warnings)


def save_review(repo: Repository, solution_id: str, review: ReviewImport, model_label: str) -> None:
    status = "flagged" if review.result.verdict == "major_issues" else "ai_checked"
    notes = json.dumps({"model": model_label, "reviewed_at": utcnow().isoformat(), **review.result.model_dump()},
                       ensure_ascii=False)
    repo.set_math_review(solution_id, notes, status)


def load_review(notes: str | None) -> dict | None:
    if not notes:
        return None
    try:
        return json.loads(notes)
    except json.JSONDecodeError:
        return {"verdict": "unknown", "summary": notes, "issues": []}
