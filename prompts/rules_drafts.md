## Task: DRAFTS — turn the tutor's approved solution into social-media drafts
The request contains the problem, the tutor's approved solution, course context, the platforms wanted and a call-to-action style (plus the tutor's profile when the style is `direct`).

1. The mathematics comes ONLY from the tutor's solution. Don't add steps, change the method or "fix" anything. If you think something is wrong, write it in `concerns` and keep the tutor's version.
2. Write one draft per requested platform. The mathematics must be the same on every platform.
3. **Xiaohongshu** (`language: "zh"`): natural Simplified Chinese, as a Chinese-speaking tutor in Canada would write. Keep course codes, university names and standard terms students use in English where natural (e.g. MAT137, eigenvalue). Each title option is at most 20 Chinese characters. Caption: 300–800 characters, short paragraphs, at most a few emoji, ending with the closing sentence (`cta`). Hashtags: 5–10, mixing Chinese and English, including course-code tags when courses are given.
4. **Instagram** (`language: "en"`): the first line is a hook that works before "more". Caption: 80–250 words. Hashtags: 5–15.
5. **Facebook** (only if requested, `language: "en"`): a short, plain post suitable for a university study group: the problem, a one-line insight and the closing sentence. 0–3 hashtags, empty `slides`.
6. `title_options`: exactly three, in this order: (1) searchable (course code and topic keywords), (2) curiosity-driven, (3) exam-focused.
7. `slides` (Xiaohongshu and Instagram): exactly 5, in this order: hook, problem, key insight, solution, closing. At most about 60 words per slide body. Maths in `$...$`. The closing slide follows the CTA style below.
8. Call to action, following the CTA style in the request:
   - `soft` (teaching only): do NOT mention tutoring, lessons, booking, sending a DM for help, or the tutor's services anywhere: not in titles, caption or slides. End with an engagement prompt about the mathematics, e.g. a variant for readers to try or a question about where they got stuck. Put that closing sentence in `cta`. The closing slide is a takeaway or a "try this" variant.
   - `direct`: a clear, calm invitation to book, naming services from the profile. The closing slide is that invitation.
   - Always: never promise grades, scores or outcomes (no "guarantee", "A+", "100%", "包过", "保证", "提分", "稳拿"). Never invent prices, discounts, reviews or student numbers. Use only credentials listed in the profile. Don't imply a university affiliation or endorsement; course codes as keywords are fine.
9. Hashtags without the `#` sign.
10. Captions and titles are plain text: the platforms do NOT render LaTeX. In captions, write any maths with Unicode (x², √10, ε, δ, ≤, →, ℝ) and keep it light. Full LaTeX belongs only in `slides`.

Output:
```json
{
  "task": "DRAFTS",
  "solution_id": "copy from the request",
  "drafts": [
    {
      "platform": "xiaohongshu | instagram | facebook",
      "language": "zh | en",
      "title_options": ["searchable", "curiosity", "exam-focused"],
      "caption": "full caption",
      "hashtags": ["MAT137", "微积分"],
      "keywords": ["search keywords"],
      "cta": "the call-to-action sentence used in the caption",
      "slides": [{"heading": "Hook", "body": "..."}]
    }
  ],
  "concerns": []
}
```
