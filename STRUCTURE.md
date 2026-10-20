# Math Tutor Studio — code structure

Last updated: 2026-10-16 · schema version 10 · AI rules version `rules_v7`

A local Streamlit + SQLite app. Pipeline:
**research → topics → candidate questions → your verified solution → bilingual posts → (results)**

## Layers

```
ui/            Streamlit pages: widgets only, no SQL, no prompt text
  │ calls
services/      Business logic: ranking, prompts, AI-reply validation, exports
collectors/    External data sources (official APIs only)
  │ call
db/            SQLAlchemy models, the Repository (all reads/writes), migrations, seed data
  │
data/tutoring.db   SQLite: the single source of truth (pandas DataFrames are views of it)
```

Rules that keep it maintainable:
- UI code never writes SQL; it calls `Repository` methods (`db/repository.py`).
- Prompt wording lives in `prompts/*.md`, not in Python.
- Every AI reply, from ChatGPT copy-paste or the API, goes through a validator before anything is saved.
- AI never writes your solution text. AI solutions stay "unverified" until you approve.

## Files

### Entry points
| File | Purpose |
|---|---|
| `app.py` | Streamlit entry: builds the pages (workflow pages wrapped with previous/next links) and the sidebar |
| `start.bat` | Double-click launcher (runs `streamlit run app.py` in `.venv`) |
| `start_demo.bat` | Same, in demo mode (`MTS_DEMO=1`): separate `data/demo.db` filled with sample content |
| `config.yaml` | Defaults: paths, AI mode, Stack Exchange settings, ranking weights, tutor profile, publishing limits |
| `.env` | OpenAI API key, only if API mode is used (git-ignored; `.env.example` is the template) |

### `core/`
| File | Purpose |
|---|---|
| `config.py` | Loads `config.yaml`; resolves paths. `MTS_DB_PATH` overrides the database (tests); `MTS_DEMO=1` switches to `data/demo.db` (`is_demo()`) |

### `db/`
| File | Purpose |
|---|---|
| `models.py` | All tables (see below) and allowed values (`SUBJECTS`, `QUESTION_TYPES`, `POST_STATUSES`, …) |
| `repository.py` | `Repository`: every read/write; returns DataFrames for display; validates input (`ValidationError`) |
| `migrations.py` | Upgrades existing databases in place (`schema_version` in `settings`); new DBs start at latest |
| `seed.py` | Initial courses (UofT MAT137/MAT223, UBC MATH 100/221) and starter topics; idempotent |

### `collectors/`
| File | Purpose |
|---|---|
| `stackexchange.py` | Math Stack Exchange `search/advanced` API client (one tag filter, relevance sort, backoff, quota) |
| `page_reader.py` | Reads one public web page or PDF (syllabus, course page) into text (PDF layout mode when it keeps word spacing better); flags login pages; `clean_contents()` strips dot leaders/page numbers from tables of contents |

### `services/`
| File | Purpose |
|---|---|
| `research.py` | `run_stackexchange()`: one search per active topic, saves/refreshes sources, logs a `research_runs` row |
| `topic_ranker.py` | `rank_topics()`: fixed-weight 5-signal score (see "Ranking") |
| `prompting.py` | Shared rules document, `build_request(task, …)`, JSON reply parsing incl. LaTeX-backslash repair |
| `question_generator.py` | QUESTIONS task: prompt from ranked topics + evidence; validates reply (schema, real topic/source IDs, near-duplicates) |
| `math_reviewer.py` | MATH_REVIEW task: AI checks your solution; reports issues only |
| `topic_suggester.py` | TOPICS task: course documents saved per course with full text — syllabus, course notes/outline (counts as coverage), textbook contents (+ chapters in scope), calendar description; optional tutor notes per round; documents can also be attached directly in ChatGPT (described in the request, recorded as a text-less source) → suggested topics with evidence type (listed / textbook_contents / unmapped / implied), anchored to the course's existing difficulty ratings; adds ticked ones to the course |
| `draft_generator.py` | DRAFTS task: bilingual posts from your approved solution; `check_post()` warnings (limits, promises, prices, LaTeX in captions, tutoring mentions in soft posts) |
| `uploads.py` | Uploaded course documents (PDF / image / text): text extraction, originals in `data/imports/`, and per-document routing — sent as a file (Claude/OpenAI in API mode), inline text, attach-in-ChatGPT, or not possible |
| `exporter.py` | Writes a Markdown package (+ rendered slide images) and a zip to `data/exports/<date>_<title>/` |
| `slides.py` | Slide images: HTML template + KaTeX, auto-fit text, theme (handle/colours), optional exact-question card on slide 2; output in `data/slides/<post_id>/` |
| `slide_worker.py` | Separate process that drives Microsoft Edge (Playwright) to screenshot each slide |
| `demo.py` | Demo content (fictional sources, three original questions, a verified solution, two drafts) loaded into an empty demo database |
| `profile.py` | Your public profile (config + Settings override): the only credentials/services drafts may use |
| `latex.py` | `normalize_math()` (`\(…\)` → `$…$`), `has_unwrapped_math()` |
| `ai_provider.py` | AI modes: `ManualChatGPTProvider` (copy-paste, default) or `APIProvider`; provider registry (OpenAI, Anthropic, DeepSeek, OpenAI-compatible), per-provider key/model/base URL settings |
| `ai_client.py` | OpenAI-compatible chat API (OpenAI, DeepSeek, custom base URL) |
| `claude_client.py` | Claude via Anthropic's SDK: default `claude-opus-5-5`, effort high, server-side `fallbacks: "default"` on models that support it, refusal handling |
| `secrets.py` | API keys in `.env`, one variable per provider |

### `ui/` (pages, in sidebar order)
| File | Page | What you do there |
|---|---|---|
| `dashboard.py` | Dashboard | Counts, pipeline status, last research run, missing current terms |
| `research.py` | Research | Stack Exchange search; topic suggestions (save course documents by link/PDF/paste, tick which to use, AI round, tick topics to add); add sources manually; browse/exclude/re-link sources |
| `questions.py` | Question Studio | Choose ranked topics → ChatGPT round → review candidates (★ ✓ ✗), edit text, copy AI solution |
| `workspace.py` | Reasoning Workspace | Write your own solution **or** start from the AI solution (verify mode) → optional AI math check → your approval (always required) |
| `publisher.py` | Publishing Studio | Drafts round (soft = teaching only / direct) → edit title/caption/hashtags/slides → render slide images → copy-ready text → status → export |
| `library.py` | Question Library | Headline numbers, questions per topic (status breakdown on hover), difficulty spread, table view |
| `courses.py` | Courses | Courses (stable), Terms & links (per-term pages, monitor flag), add course |
| `topics.py` | Topics | Topic table (incl. Stack Exchange search query), add topic, course links |
| `settings.py` | Settings | AI mode (ChatGPT copy-paste or API + provider), ChatGPT Project rules, tutor profile, slide style (+ preview), ranking weights, provider keys/models, DB backup |
| `nav.py` | (component) | Previous/next links along the workflow: Dashboard → Research → Question Studio → Reasoning Workspace → Publishing Studio |
| `ai_round.py` | (component) | One AI exchange: build prompt → copy/paste or API → validate & import |
| `forms.py` | (component) | Add-course / add-term forms (Courses and Research pages) |
| `common.py` | (helpers) | Cached `get_repo()`, label maps, `copyable()` copy button (st.html), `section()` foldable headings (fold state saved in `settings`) |

### `prompts/`
| File | Purpose |
|---|---|
| `rules_common.md` | Role, honesty rules, maths formatting, JSON code-block output |
| `rules_questions.md` / `rules_math_review.md` / `rules_drafts.md` / `rules_topics.md` | Rules and output schema for each task |
| `question_request.md` | Data template for a QUESTIONS request |

Changing any `rules_*.md` meaningfully → bump `RULES_VERSION` in `services/prompting.py`; the app then
asks you to re-copy the rules into your ChatGPT Project.

### `assets/`
| Path | Purpose |
|---|---|
| `assets/katex/` | KaTeX 0.16.11 (MIT, bundled for offline slide rendering) |

### `tests/`
`pytest` (run: `.venv\Scripts\python.exe -m pytest`). Covers the repository, migrations, LaTeX handling,
reply validation, research (mocked HTTP), ranking, drafts/review/export, secrets, real slide rendering with
Edge (skipped if Edge is missing), verify mode, and headless UI runs of every page (`test_ui.py`, temporary database).

## Database tables
| Table | One row is… | Key links |
|---|---|---|
| `courses` | a course as it stays from year to year | → `course_offerings`, ↔ `topics` via `course_topics` |
| `course_offerings` | one term (year, season, section) with its own URLs, exam notes, `monitor` flag | → `courses` |
| `topics` | a mathematical topic (difficulty relative to its course, target level, optional `search_query`) | ↔ `courses`, ↔ `sources` via `source_topics` |
| `sources` | a piece of evidence (SE post, course page, manual note) or a course document (`full_text` set; `doc_scope` = chapters in scope for textbook contents; `file_path` = uploaded original); dedup by `platform_id`/`content_hash` | → `courses`, `course_offerings`; ↔ `topics` |
| `research_runs` | one collector run, success or failure, with log and API quota | — |
| `topic_scores` | a saved ranking of a topic at the time a question batch was made | → `topics` |
| `questions` | an AI candidate problem (`review_status`, `selection_status`, `batch_id`, cited `source_ids`) | → `topics` |
| `solutions` | the solution for a question (one per question); `origin` own / ai_verified; AI review notes; `approved` | → `questions` |
| `solution_revisions` | a snapshot at every save; `author` you / ai (the imported AI draft) | → `solutions` |
| `posts` | one platform draft (title options, caption, hashtags, slides, flags, status, `cta_style`, `use_question_card`) | → `solutions` |
| `metrics` | a performance snapshot of a post *(table exists; UI not built yet)* | → `posts` |
| `settings` | key/value preferences (AI mode, weights, profile, rules version, schema version) | — |

## Difficulty scale
Topic and question difficulty (1–5) are **relative to the course**: 1 = routine exercise for that course,
3 = typical exam question, 5 = among the hardest in it. How advanced a course is lives in the course level
and the topic's `target_level`. Prompts send a course's existing ratings as anchors.

## Ranking (no statistical model)
`S = 100 · Σ wᵢxᵢ / Σ wᵢ` over five 0–1 signals: course relevance (priority/5), observed difficulty
(source count and log-votes; 0.3 neutral prior when there is no evidence), timeliness (monitored current
term), content potential (prior by level), novelty (days since used, reduced by open candidates).
Weights are assumptions, editable in Settings.

## AI tasks and statuses
| Task | Triggered from | Validated by | Result |
|---|---|---|---|
| QUESTIONS | Question Studio | `question_generator.validate_reply` | `questions` (candidate, unreviewed) |
| MATH_REVIEW | Reasoning Workspace | `math_reviewer.parse_review` | `solutions.math_review_notes`; question → ai_checked / flagged |
| DRAFTS | Publishing Studio | `draft_generator.parse_drafts` | `posts` (draft, with flags) |
| TOPICS | Research → Topic suggestions | `topic_suggester.parse_topic_suggestions` | ticked rows → `topics` |

- Question `selection_status`: candidate → favorite / selected / rejected. *Open* (candidate+favorite) lowers a topic's novelty; *selected* marks the topic used; *rejected* stays stored so the duplicate check never regenerates it
- Question `review_status`: unreviewed → ai_checked / flagged → verified (your approval)
- Post `status`: draft → needs_review → approved → published (also archived / rejected)

## Files outside the code
| Path | Contents |
|---|---|
| `data/tutoring.db` | the database, created and seeded automatically if missing. Recent changes may sit in `tutoring.db-wal`, so never copy the .db file alone: use Settings → Download database backup (`Repository.backup_bytes`). `tutoring.backup-before-vN.db` = automatic pre-upgrade copies |
| `data/imports/` | uploaded document originals (`<source_id>.<ext>`) |
| `data/demo.db` | demo-mode database (git-ignored) |
| `docs/screenshots/` | README screenshots and example slides, captured from demo mode |
| `data/slides/<post_id>/` | rendered slide PNGs + `slides.json` (what they were rendered from) |
| `data/exports/` | exported publication packages |

## Not built yet
Post metrics and anonymous inquiry-outcome tracking · AI triage of collected sources · course renames /
code changes (aliases, per-term code/title, "replaced by") · handwritten-slide upload · scheduler · website export.
