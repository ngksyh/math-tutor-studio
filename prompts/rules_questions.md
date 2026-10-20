## Task: QUESTIONS — write original candidate problems
The request lists topics with evidence. Write the requested number of ORIGINAL candidate problems. The tutor will pick one, solve it in their own words, and publish their explanation.

1. The problems must be original. Use the listed sources only to understand what students find confusing. Do not copy or lightly reword a source's question.
2. Each problem should target a real conceptual difficulty, a common misconception or a classic exam trap, so that a clear explanation teaches something.
3. Vary difficulty (relative to the topic's course, see the general rules; if a topic lists several courses, use the first) and question types, unless the tutor's preferences in the request say otherwise. Avoid near-duplicates.
4. A problem must suit a short post: its solution should fit on 3–4 slides.
5. In `source_ids`, list ONLY ids from the "Evidence" lines of that problem's topic. If no listed source supports it, use an empty list.
6. Do not claim that students commonly struggle with something unless a listed source supports it. Phrase unsupported points as teaching judgment.
7. `proposed_solution` is a draft for the tutor to check. Be correct and concise.
8. Follow the topics given; don't add unrelated ones. Follow the tutor's preferences when present.

Output:
```json
{
  "task": "QUESTIONS",
  "batch_id": "copy the batch_id from the request",
  "questions": [
    {
      "topic_id": "one of the topic ids in the request",
      "title": "short descriptive title",
      "question_latex": "full problem statement",
      "question_type": "one of: <<QUESTION_TYPES>>",
      "difficulty": 3,
      "expected_time_min": 10,
      "target_audience": "one of: <<TARGET_LEVELS>>",
      "proposed_solution": "concise worked solution",
      "teaching_insight": "the key idea or misconception this problem exposes",
      "source_ids": ["src_..."]
    }
  ]
}
```
