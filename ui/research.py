from datetime import date, datetime, time

import pandas as pd
import streamlit as st

from collectors.page_reader import ReadError, fetch_document, pasted_document
from core.config import load_config
from db.models import TARGET_LEVELS
from db.repository import ValidationError
from services.ai_provider import get_provider
from services.research import run_stackexchange, search_plan
from services.topic_suggester import (
    DOC_TYPES,
    add_suggested_topics,
    build_topics_prompt,
    parse_topic_suggestions,
    record_attachment,
    save_document,
    update_document,
)
from services.uploads import MAX_MB, UPLOAD_TYPES, load_part, plan_routes, read_upload, store_file
from ui.ai_round import ai_round
from ui.common import course_label_map, get_repo, section, text_or_empty, topic_label_map
from ui.forms import MIN_DATE, add_course_form, add_offering_form

NO_SCOPE = (None, None)


def _scope_label(choices: dict, key: tuple) -> str:
    return "— not course-specific —" if key == NO_SCOPE else choices[key]


def _add_tab(repo, source_types: list[str]) -> None:
    with st.expander("Course or term not listed? Add it here"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**New course**")
            add_course_form(repo, key="research")
        with c2:
            st.markdown("**New term for an existing course**")
            add_offering_form(repo, key="research")

    scopes = repo.course_scope_choices()
    topics = topic_label_map(repo)
    with st.form("add_source", clear_on_submit=True):
        c1, c2 = st.columns([1, 2])
        source_type = c1.selectbox("Source type", source_types)
        title = c2.text_input("Title *", placeholder="MAT464 Fall 2011 course page")
        url = st.text_input("URL", placeholder="https://...")
        c3, c4 = st.columns(2)
        scope = c3.selectbox("Course / term", [NO_SCOPE, *scopes],
                             format_func=lambda k: _scope_label(scopes, k),
                             help="Pick a specific term if the page belongs to one; otherwise 'any term'.")
        topic_ids = c4.multiselect("Related topics", list(topics), format_func=topics.get)
        excerpt = st.text_area("Relevant excerpt (short — quote only what you need)", height=120)
        c5, c6, c7 = st.columns(3)
        published = c5.date_input("Published on", value=None, min_value=MIN_DATE, max_value=date.today())
        engagement = c6.number_input("Votes / engagement", min_value=0, value=None, step=1,
                                     help="Leave blank if unknown — blank is not treated as zero.")
        license_ = c7.text_input("License", placeholder="CC BY-SA 4.0")
        notes = st.text_input("Your notes (optional)")
        if st.form_submit_button("Save source", type="primary"):
            course_id, offering_id = scope
            try:
                sid, created = repo.add_source(
                    source_type=source_type, title=title, url=url, course_id=course_id,
                    offering_id=offering_id, content_excerpt=excerpt,
                    published_at=datetime.combine(published, time()) if published else None,
                    engagement_count=int(engagement) if engagement is not None else None,
                    license=license_, notes=notes, topic_ids=topic_ids,
                )
                if created:
                    st.success(f"Saved {sid}.")
                else:
                    st.warning(f"This source is already stored ({sid}); nothing added.")
            except ValidationError as e:
                st.error(str(e))


def _browse_tab(repo, source_types: list[str]) -> None:
    df = repo.sources_df()
    if df.empty:
        st.info("No sources yet.")
        return
    courses = course_label_map(repo)
    topics = topic_label_map(repo)

    f1, f2, f3 = st.columns(3)
    course_filter = f1.selectbox("Course ", ["All", *courses], format_func=lambda c: courses.get(c, c))
    type_filter = f2.selectbox("Type", ["All", *source_types])
    show_excluded = f3.toggle("Show excluded", value=False)
    view = df
    if course_filter != "All":
        view = view[view["course_id"] == course_filter]
    if type_filter != "All":
        view = view[view["source_type"] == type_filter]
    if not show_excluded:
        view = view[~view["excluded"]]

    view = view.assign(course=view["course_id"].map(courses))
    cols = ["source_id", "title", "source_type", "course", "term", "topics", "engagement_count",
            "published_at", "url", "excluded", "notes"]
    edited = st.data_editor(
        view[cols],
        hide_index=True,
        width="stretch",
        disabled=[c for c in cols if c not in ("excluded", "notes")],
        column_config={
            "source_id": None,
            "title": st.column_config.TextColumn("Title", width="large"),
            "source_type": st.column_config.TextColumn("Type"),
            "course": st.column_config.TextColumn("Course"),
            "term": st.column_config.TextColumn("Term"),
            "topics": st.column_config.TextColumn("Topics"),
            "engagement_count": st.column_config.NumberColumn("Engagement"),
            "published_at": st.column_config.DatetimeColumn("Published", format="YYYY-MM-DD"),
            "url": st.column_config.LinkColumn("Link", display_text="open"),
            "excluded": st.column_config.CheckboxColumn("Exclude"),
            "notes": st.column_config.TextColumn("Notes"),
        },
        key="source_editor",
    )
    if st.button("Save changes", type="primary"):
        n = repo.update_sources(edited)
        st.success(f"Saved ({n} field(s) changed).")

    st.divider()
    if not section("Edit a source", "res_edit"):
        return
    labels = dict(zip(view["source_id"], view["title"]))
    if not labels:
        return
    sid = st.selectbox("Select a source", list(labels), format_func=labels.get)
    row = df[df["source_id"] == sid].iloc[0]
    if url := text_or_empty(row["url"]):
        st.markdown(f"[Open original]({url})")
    if excerpt := text_or_empty(row["content_excerpt"]):
        st.markdown("> " + excerpt.replace("\n", "\n> "))
    if notes := text_or_empty(row["notes"]):
        st.caption(notes)

    scopes = repo.course_scope_choices()
    as_id = lambda v: v if isinstance(v, str) else None  # noqa: E731 — NaN/None -> None
    current_scope = (as_id(row["course_id"]), as_id(row["offering_id"])) if as_id(row["course_id"]) else NO_SCOPE
    options = [NO_SCOPE, *scopes]
    new_scope = st.selectbox("Course / term", options,
                             index=options.index(current_scope) if current_scope in options else 0,
                             format_func=lambda k: _scope_label(scopes, k), key=f"scope_{sid}")
    new_topics = st.multiselect("Linked topics", list(topics), default=row["topic_ids"],
                                format_func=topics.get, key=f"st_{sid}")
    b1, b2 = st.columns([1, 5])
    if b1.button("Save", type="primary"):
        repo.set_source_scope(sid, *new_scope)
        repo.set_source_topics(sid, new_topics)
        st.success("Saved.")
        st.rerun()
    if b2.button("Delete source", help="Prefer 'Exclude' unless this was entered by mistake."):
        repo.delete_source(sid)
        st.rerun()


def _auto_tab(repo) -> None:
    st.markdown("Searches **Mathematics Stack Exchange** (official API, free) for each active topic and "
                "stores the most-voted matching questions as evidence, linked to that topic. "
                "Re-running updates vote counts instead of creating duplicates.")
    plan = search_plan(repo)
    if plan.empty:
        st.info("No active topics to search.")
        return
    st.caption("Search terms come from each topic's *Search query* (Topics page), or its name if empty. "
               "Short queries work best. The tag comes from the subject (config.yaml → "
               "stackexchange.subject_tags).")
    edited = st.data_editor(
        plan.assign(run=True), hide_index=True, width="stretch",
        column_order=["run", "topic", "subject", "query", "tag"],
        disabled=["topic", "subject", "query", "tag"],
        column_config={"run": st.column_config.CheckboxColumn("Search"),
                       "query": st.column_config.TextColumn("Query", width="medium"),
                       "tag": st.column_config.TextColumn("Tag filter")},
        key="se_plan",
    )
    chosen = list(edited.loc[edited["run"], "topic_id"])
    st.caption(f"Uses about {len(chosen)} of the 300 free API requests per day.")
    if st.button("Run research now", type="primary", disabled=not chosen):
        bar = st.progress(0.0, text="Starting…")
        summary = run_stackexchange(repo, topic_ids=chosen, progress=lambda f, t: bar.progress(f, text=t))
        msg = (f"{summary.sources_added} new source(s), {summary.sources_refreshed} refreshed, "
               f"{summary.requests_made} request(s); quota left today: {summary.quota_remaining}.")
        {"ok": st.success, "partial": st.warning, "failed": st.error}[summary.status](
            f"Run {summary.status}: {msg}")
        with st.expander("Details", expanded=summary.status != "ok"):
            st.text("\n".join(summary.log))

    runs = repo.runs_df()
    if not runs.empty:
        if not section("Recent runs", "res_runs"):
            return
        st.dataframe(
            runs.drop(columns=["run_id"]), hide_index=True, width="stretch",
            column_config={"started_at": st.column_config.DatetimeColumn("Started", format="YYYY-MM-DD HH:mm"),
                           "finished_at": None, "log": st.column_config.TextColumn("Log", width="large")},
        )


def _add_document_form(repo, scopes: dict, course_id: str, offering_id: str | None) -> None:
    with st.container(border=True):
        st.markdown("**Add a document**")
        c1, c2 = st.columns([1, 2])
        doc_type = c1.selectbox("Type", list(DOC_TYPES), format_func=DOC_TYPES.get, key="sg_type",
                                help="For a syllabus that only cites chapters, also add the textbook's table of "
                                     "contents (copy it from the publisher's page or the book's contents pages).")
        title = c2.text_input("Name (optional)", key="sg_title",
                              placeholder="e.g. Stewart, Calculus 9e — table of contents")
        scope = None
        if doc_type == "textbook_contents":
            scope = st.text_input("Chapters/sections in scope", key="sg_scope_in",
                                  placeholder="all — or e.g. 1–6, 9.1–9.3",
                                  help="Which parts of the book this course covers. Leave empty if a syllabus "
                                       "you add says so instead.")
        elif doc_type == "course_notes":
            st.caption("Course notes / outline: everything in it counts as course coverage.")
        how = st.radio("From", ["Upload a file", "Link (web page or PDF)", "Paste text"], horizontal=True,
                       key="sg_how")
        doc, upload = None, None
        if how.startswith("Upload"):
            upload = st.file_uploader(
                "PDF, image (PNG/JPG/WEBP) or text file", type=UPLOAD_TYPES, max_upload_size=MAX_MB,
                key="sg_file",
                help="A few pages work best. The original is kept with the course's documents: in API mode "
                     "Claude or OpenAI receive the file itself; otherwise its text is used, or you attach it "
                     "in ChatGPT.")
            if st.button("Save file", disabled=upload is None):
                try:
                    doc = read_upload(upload.name, upload.getvalue())
                except ValueError as e:
                    st.error(str(e))
        elif how.startswith("Link"):
            url = st.text_input("Link", placeholder="https://…/syllabus.pdf", key="sg_url")
            if st.button("Read and save", disabled=not url.strip()):
                try:
                    with st.spinner("Reading…"):
                        doc = fetch_document(url)
                except ReadError as e:
                    st.error(str(e))
        else:
            text = st.text_area("Text", height=180, key="sg_text",
                                help="For pages behind a login (Quercus, Canvas) or a book's contents pages.")
            if st.button("Save text", disabled=not text.strip()):
                doc = pasted_document(text, title.strip() or f"{DOC_TYPES[doc_type]} — {scopes[(course_id, None)]}")
        if doc is not None:
            for w in doc.warnings:
                st.warning(w)
            sid = save_document(repo, doc, course_id=course_id, offering_id=offering_id, doc_type=doc_type,
                                title=title.strip() or None, scope=scope)
            if upload is not None:
                store_file(repo, sid, upload.name, upload.getvalue())
            st.session_state["sg_result"] = None
            words = len(doc.text.split())
            st.success(f"Saved **{title.strip() or doc.title}**"
                       + (f" ({words} words of text)" if words else " (file only — no extractable text)") + ".")


def _suggest_tab(repo) -> None:
    st.markdown("Save the course's **documents** (syllabus, the textbook's table of contents, the calendar "
                "description), then let ChatGPT suggest topics from them. It may only use what you supply, "
                "never its memory of a textbook. Documents show what a course covers; the Stack Exchange "
                "search shows where students struggle.")
    scopes = repo.course_scope_choices()
    if not scopes:
        st.info("Add a course first (Courses page).")
        return
    scope = st.selectbox("Course / term", list(scopes), format_func=scopes.get, key="sg_scope")
    course_id, offering_id = scope

    _add_document_form(repo, scopes, course_id, offering_id)

    docs = repo.course_documents_df(course_id)
    chosen: list[dict] = []
    if docs.empty:
        st.info("No documents saved for this course yet — save one above, or attach it in ChatGPT (below).")
    else:
        chosen = _saved_documents(repo, docs, course_id)

    attachments = _attachment_form(repo, course_id)

    notes = st.text_area("Notes for ChatGPT (optional)", key="sg_notes", height=80,
                         placeholder="e.g. These are the official MAT137 course notes. This term covers "
                                     "chapters 2–5 only.",
                         help="Anything ChatGPT should know about these documents. Your notes override the "
                              "documents.")
    n = st.slider("Suggest up to", 3, 25, 10, key="sg_n",
                  help="For a long contents page, consider one chapter range per round.")

    def handle(reply: str, model: str) -> None:
        st.session_state["sg_result"] = parse_topic_suggestions(repo, reply, course_id=course_id)
        st.session_state["sg_result_course"] = course_id

    provider = get_provider(repo)
    plan = plan_routes(chosen, accepts=provider.accepts, manual=provider.is_manual)
    blocked = _show_routes(plan, provider)

    def build(full: bool) -> tuple[str, list]:
        prompt = build_topics_prompt(
            repo, course_id, [r.doc for r in plan.by("text")], max_topics=n, include_rules=full, notes=notes,
            sent_files=[r.doc for r in plan.by("file")],
            attachments=[r.doc for r in plan.by("attach")] + attachments)
        if attachments and st.session_state.get("sg_att_record", True):
            for a in attachments:
                record_attachment(repo, course_id=course_id, offering_id=offering_id, doc_type=a["source_type"],
                                  title=a["title"], scope=a["doc_scope"])
        return prompt, [r.part for r in plan.by("file")]

    ai_round(repo, "topics", manual_label="Build topic prompt", api_label="Suggest topics via API",
             build=build, handle=handle, disabled=blocked or not (chosen or attachments))
    if attachments or plan.by("attach"):
        st.caption(":material/attach_file: Remember to attach the file(s) to the same ChatGPT message as the prompt.")
    _suggestion_results(repo, scopes, course_id)


def _attachment_form(repo, course_id: str) -> list[dict]:
    """Describe a document you'll attach directly in ChatGPT (e.g. a few PDF pages or a scan)."""
    if not get_provider(repo).is_manual:
        return []  # attachments only exist in the ChatGPT copy-and-paste mode
    if not st.checkbox("I'll also attach a document directly in ChatGPT", key="sg_att"):
        return []
    with st.container(border=True):
        c1, c2 = st.columns([1, 2])
        doc_type = c1.selectbox("Type", list(DOC_TYPES), format_func=DOC_TYPES.get, key="sg_att_type")
        title = c2.text_input("What is it?", key="sg_att_title",
                              placeholder="e.g. MAT137 course notes, pages 3–5 (scan)")
        scope = None
        if doc_type == "textbook_contents":
            scope = st.text_input("Chapters/sections in scope", key="sg_att_scope", placeholder="all — or e.g. 1–6")
        st.checkbox("Keep a record of it in the app (title and type only; the text isn't stored)",
                    value=True, key="sg_att_record")
        st.caption("Good for a few pages or scanned PDFs. For something you'll reuse (a contents page), "
                   "saving it above is better: the app keeps the text for later rounds.")
    if not title.strip():
        st.caption("Describe the attachment to include it in the prompt.")
        return []
    return [{"title": title.strip(), "source_type": doc_type, "doc_scope": (scope or "").strip() or None}]


def _show_routes(plan, provider) -> bool:
    """Explain how each chosen document will reach the AI. Returns True if the round can't run."""
    if sent := plan.by("file"):
        st.caption(f":material/upload_file: Sent to {provider.model_label} as files: "
                   + ", ".join(r.doc["title"] for r in sent))
    if attach := plan.by("attach"):
        st.info("These have no extractable text, so attach them in ChatGPT together with the prompt:")
        for r in attach:
            st.download_button(f"Download {r.doc['title']}", r.part.data, key=f"dl_{r.doc['source_id']}",
                               file_name=f"{r.doc['title']}.{r.part.mime.split('/')[-1]}", mime=r.part.mime,
                               icon=":material/download:")
    if impossible := plan.by("impossible"):
        st.error("Can't send: " + ", ".join(r.doc["title"] for r in impossible)
                 + f" — no extractable text, and {provider.model_label} can't read files. Untick them, use "
                   "ChatGPT mode, or choose Claude/OpenAI in Settings.")
        return True
    return False


def _saved_documents(repo, docs, course_id: str) -> list[dict]:
    st.markdown("**Documents for this course** — tick the ones to use")
    docs = docs.assign(file=docs["file_path"].map(
        lambda p: p.rsplit(".", 1)[-1].upper() if isinstance(p, str) and p else ""))
    picked = st.data_editor(
        docs.assign(use=True), hide_index=True, width="stretch", key=f"sg_docs_{course_id}",
        column_order=["use", "source_type", "title", "doc_scope", "term", "words", "file", "url"],
        disabled=["title", "term", "words", "file", "url"],
        column_config={"use": st.column_config.CheckboxColumn("Use"),
                       "source_type": st.column_config.SelectboxColumn(
                           "Type", options=list(DOC_TYPES), required=True,
                           help="syllabus · course_notes (the course's own) · textbook_contents · "
                                "calendar_description · other. Edit, then save."),
                       "title": st.column_config.TextColumn("Document", width="large"),
                       "doc_scope": st.column_config.TextColumn(
                           "In scope", help="Textbook contents only: 'all' or e.g. '1–6, 9'. Edit, then save."),
                       "file": st.column_config.TextColumn("File", help="Uploaded original, if any"),
                       "url": st.column_config.LinkColumn("Link", display_text="open")},
    )
    as_text = lambda v: v.strip() if isinstance(v, str) and v.strip() else None  # noqa: E731 — None/NaN/"" alike
    changed = [r for r, old in zip(picked.to_dict("records"), docs.to_dict("records"))
               if as_text(r["doc_scope"]) != as_text(old["doc_scope"]) or r["source_type"] != old["source_type"]]
    if changed and st.button("Save document changes"):
        for r in changed:
            update_document(repo, r["source_id"], doc_type=r["source_type"], scope=as_text(r["doc_scope"]),
                            full_text=r["full_text"])
        st.rerun()
    chosen = picked[picked["use"]].to_dict("records")
    with st.expander("Preview the chosen documents"):
        for d in chosen:
            st.markdown(f"**{d['title']}**")
            part = load_part(d)
            if part and part.mime.startswith("image/"):
                st.image(part.data, width=320, alt=f"Uploaded page: {d['title']}")
            elif d["full_text"]:
                st.text(d["full_text"][:1500] + ("\n…" if len(d["full_text"]) > 1500 else ""))
            else:
                st.caption("No text (file only).")
    st.caption("Remove a document under **Browse sources** (tick Exclude).")
    return chosen


def _suggestion_results(repo, scopes: dict, course_id: str) -> None:
    result = st.session_state.get("sg_result")
    if not result or st.session_state.get("sg_result_course") != course_id:
        return
    for w in result.warnings:
        st.warning(w)
    if result.notes:
        st.info(f"ChatGPT's notes: {result.notes}")
    for r in result.rejected:
        st.error(r)
    if not result.topics:
        st.info("No topics suggested.")
        return
    st.markdown("**Suggested topics.** Edit before adding. *Unmapped* (book part not known to be in scope) "
                "and *implied* topics, and look-alikes of existing topics, start unticked. Difficulty is "
                "relative to this course.")
    edited = st.data_editor(
        pd.DataFrame(result.topics), hide_index=True, width="stretch", key="sg_editor",
        disabled=["evidence_type", "syllabus_evidence", "similar_to"],
        column_order=["add", "name", "subject", "target_level", "difficulty_level", "evidence_type",
                      "syllabus_evidence", "search_query", "student_difficulty", "concept_description", "similar_to"],
        column_config={
            "add": st.column_config.CheckboxColumn("Add"),
            "name": st.column_config.TextColumn("Topic", width="medium"),
            "target_level": st.column_config.SelectboxColumn("Level", options=TARGET_LEVELS),
            "difficulty_level": st.column_config.NumberColumn("Difficulty", min_value=1, max_value=5),
            "evidence_type": st.column_config.TextColumn("Evidence"),
            "syllabus_evidence": st.column_config.TextColumn("From the documents", width="large"),
            "search_query": st.column_config.TextColumn("Search query"),
            "student_difficulty": st.column_config.TextColumn("Likely difficulty", width="large"),
            "concept_description": st.column_config.TextColumn("Concept"),
            "similar_to": st.column_config.TextColumn("Similar existing topic"),
        },
    )
    if st.button("Add ticked topics", type="primary"):
        try:
            ids = add_suggested_topics(repo, edited.to_dict("records"), course_id=course_id)
            st.session_state["sg_result"] = None
            st.success(f"Added {len(ids)} topic(s) to {scopes[(course_id, None)].replace(' (any term)', '')}. "
                       "Run the Stack Exchange search to collect evidence for them.")
        except ValidationError as e:
            st.error(str(e))


def render() -> None:
    repo = get_repo()
    st.title("Research")
    st.caption("Record evidence of what students are struggling with. Keep the original link — "
               "the question generator will only cite sources stored here.")
    source_types = load_config()["research"]["source_types"]
    tab_auto, tab_suggest, tab_add, tab_browse = st.tabs(["Automatic (Stack Exchange)", "Topic suggestions (AI)",
                                                          "Add source manually", "Browse sources"])
    with tab_auto:
        _auto_tab(repo)
    with tab_suggest:
        _suggest_tab(repo)
    with tab_add:
        _add_tab(repo, source_types)
    with tab_browse:
        _browse_tab(repo, source_types)
