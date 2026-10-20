## Task: MATH_REVIEW — check the tutor's solution
The request contains a problem and the tutor's solution (reasoning, intuition, common mistakes, alternative methods).

1. Check correctness, rigour (hypotheses verified, quantifiers in the right order, edge cases, strict vs non-strict inequalities) and completeness for the stated problem.
2. Report each issue with its location (quote a short phrase from the solution), a description and a concrete suggestion. Severity: `major` = wrong, or an essential step missing; `minor` = a gap a careful grader would mark; `style` = clarity, notation or presentation.
3. Don't rewrite the whole solution, and don't propose a different method unless the tutor's method is invalid.
4. If you are not certain something is an error, say "possible" in the description and use `minor`.
5. Also check that the stated intuition and common mistakes are accurate.
6. If you find nothing wrong, return an empty issues list with verdict `no_issues_found`. Don't invent issues to seem thorough.

Output:
```json
{
  "task": "MATH_REVIEW",
  "solution_id": "copy from the request",
  "verdict": "no_issues_found | minor_issues | major_issues",
  "summary": "two or three sentences",
  "issues": [
    {"severity": "major | minor | style", "location": "short quote", "description": "what is wrong", "suggestion": "how to fix it"}
  ]
}
```
