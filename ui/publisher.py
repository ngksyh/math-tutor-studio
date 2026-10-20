import streamlit as st

from core.config import load_config
from db.models import CTA_STYLES, PLATFORMS, POST_STATUSES
from db.repository import ValidationError
from services.draft_generator import build_drafts_prompt, check_post, copy_text, new_round_id, parse_drafts
from services.exporter import export_package
from services.latex import normalize_math
from services.slides import existing_images, images_outdated, images_zip, render_post, slide_size
from ui.ai_round import ai_round
from ui.common import get_repo, section, text_or_empty

PLATFORM_NAMES = {"xiaohongshu": "Xiaohongshu 小红书", "instagram": "Instagram", "facebook": "Facebook group"}


def _pick_solution(repo) -> dict | None:
    solutions = repo.solutions_df(approved_only=True)
    if solutions.empty:
        st.info("No approved solutions yet. Write and approve one in the **Reasoning Workspace**.")
        return None
    labels = {r.solution_id: f"{r.title} · {r.topic} ({r.post_count} post{'s' if r.post_count != 1 else ''})"
              for r in solutions.itertuples()}
    sid = st.selectbox("Approved solution", list(labels), format_func=labels.get)
    return solutions.set_index("solution_id").loc[sid].to_dict() | {"solution_id": sid}


def _generate(repo, sol: dict) -> None:
    if not section("1 · Generate drafts", "pub_generate"):
        return
    pub = load_config().get("publishing", {})
    c1, c2 = st.columns(2)
    platforms = c1.multiselect("Platforms", PLATFORMS, default=pub.get("default_platforms", PLATFORMS[:2]),
                               format_func=PLATFORM_NAMES.get)
    cta_style = c2.radio("Call to action", CTA_STYLES, horizontal=True,
                         format_func={"soft": "Soft (teaching only)", "direct": "Direct (invite to book)"}.get,
                         help="Soft posts never mention tutoring — they end with a maths question for readers.")
    posts = repo.posts_df()
    if not posts.empty:
        direct = int((posts.drop_duplicates("draft_round")["cta_style"] == "direct").sum())
        rounds = posts["draft_round"].nunique()
        c2.caption(f"So far {direct} of {rounds} draft rounds were direct (plan: about 1 in 5).")

    def handle(reply: str, model: str) -> None:
        result = parse_drafts(reply, expected_solution_id=sol["solution_id"], platforms=platforms,
                              cta_style=cta_style)
        if result.drafts:
            repo.add_posts(sol["solution_id"], result.drafts, draft_round=new_round_id(), cta_style=cta_style)
        st.session_state["draft_report"] = result

    ai_round(repo, "drafts", manual_label="Build drafts prompt", api_label="Generate drafts via API",
             build=lambda full: build_drafts_prompt(repo, sol["solution_id"], platforms=platforms,
                                                    cta_style=cta_style, include_rules=full),
             handle=handle, disabled=not platforms)

    if report := st.session_state.get("draft_report"):
        if report.drafts:
            st.success(f"Imported {len(report.drafts)} draft(s). Edit them below.")
        for w in report.warnings:
            st.warning(w)
        for r in report.rejected:
            st.error(r)
        if report.concerns:
            st.warning("**The AI raised concerns about your solution** (your text was not changed):\n\n" +
                       "\n".join(f"- {normalize_math(c)}" for c in report.concerns))


def _slide_images(repo, post: dict) -> None:
    pid = post["post_id"]
    w, h = slide_size(post["platform"])
    st.markdown(f"**Slide images** ({w}×{h}) — made from the last *saved* slides")
    use_card = st.toggle("Slide 2: show the exact question on a problem card", value=bool(post["use_question_card"]),
                         key=f"card_{pid}",
                         help="Replaces the AI-written problem slide with your stored question, word for word, on "
                              "a decorated card. The stored question is in English.")
    if use_card != bool(post["use_question_card"]):
        repo.update_post(pid, use_question_card=use_card)
        post = {**post, "use_question_card": use_card}
    if st.button("Render slide images", key=f"render_{pid}"):
        with st.spinner("Rendering with Edge…"):
            result = render_post(repo, post)
        if result.error:
            st.error(f"Rendering failed: {result.error}")
        elif result.overflow:
            st.warning(f"Slide(s) {', '.join(map(str, result.overflow))} still don't fit at the smallest text "
                       "size — shorten them and render again.")
    images = existing_images(pid)
    if not images:
        return
    if images_outdated(repo, post):
        st.caption("ℹ️ The slides were edited after these images were made — render again.")
    cols = st.columns(len(images))
    for col, path in zip(cols, images):
        col.image(str(path), caption=path.stem.replace("_", " "))
    st.download_button("Download images (.zip)", images_zip(pid), file_name=f"{post['platform']}_slides.zip",
                       mime="application/zip", key=f"imgzip_{pid}")


def _post_editor(repo, post: dict) -> None:
    pid, platform = post["post_id"], post["platform"]
    limits = load_config().get("publishing", {}).get("limits", {}).get(platform, {})
    for flag in post["flags"] or []:
        st.warning(flag, icon="⚠️")

    options = list(dict.fromkeys([text_or_empty(post["title"]), *(post["title_options"] or [])]))
    title = st.selectbox("Title", options, index=0, accept_new_options=True, key=f"title_{pid}",
                         help="Choose a suggested title or type your own. "
                              "Order of suggestions: searchable · curiosity · exam-focused.")
    caption = st.text_area("Caption", value=text_or_empty(post["caption"]), height=260, key=f"cap_{pid}")
    st.caption(f"{len(caption)} characters" + (f" / {limits['caption_chars']}" if limits.get("caption_chars") else ""))
    hashtags = st.text_input("Hashtags (space-separated, # optional)", key=f"tags_{pid}",
                             value=" ".join(f"#{t}" for t in post["hashtags"] or []))
    keywords = st.text_input("Search keywords (comma-separated)", value=", ".join(post["keywords"] or []),
                             key=f"kw_{pid}")

    slides = post["slides"] or []
    new_slides = []
    if slides:
        st.markdown("**Slides**")
        for i, slide in enumerate(slides):
            with st.container(border=True):
                left, right = st.columns(2)
                heading = left.text_input(f"Slide {i + 1} heading", value=slide.get("heading", ""),
                                          key=f"sh_{pid}_{i}")
                body = left.text_area("Body", value=slide.get("body", ""), height=110, key=f"sb_{pid}_{i}")
                right.markdown(f"**{heading}**")
                right.markdown(normalize_math(body))
                new_slides.append({"heading": heading, "body": body})

    if slides:
        _slide_images(repo, post)

    c1, c2 = st.columns(2)
    status = c1.selectbox("Status", POST_STATUSES, index=POST_STATUSES.index(post["status"]), key=f"st_{pid}",
                          help="draft → needs_review → approved → published")
    url = c2.text_input("Published URL (after posting)", value=text_or_empty(post["published_url"]), key=f"url_{pid}")

    b1, b2 = st.columns([1, 4])
    if b1.button("Save", type="primary", key=f"save_{pid}"):
        tags = [t.lstrip("#") for t in hashtags.replace(",", " ").split() if t.lstrip("#")]
        fields = dict(title=title or "", caption=caption, hashtags=tags,
                      keywords=[k.strip() for k in keywords.split(",") if k.strip()],
                      slides=new_slides, status=status, published_url=url.strip() or None)
        fields["flags"] = check_post(platform, title=fields["title"], title_options=post["title_options"] or [],
                                     caption=caption, hashtags=tags, cta=text_or_empty(post["cta"]), slides=new_slides,
                                     cta_style=post["cta_style"])
        try:
            repo.update_post(pid, **fields)
            st.rerun()
        except ValidationError as e:
            st.error(str(e))
    with b2.popover("Delete draft"):
        if st.button("Delete this draft", key=f"del_{pid}"):
            repo.delete_post(pid)
            st.rerun()

    st.markdown("**Copy-ready text** (copy icon at top-right) — reflects the last *saved* version")
    st.code(copy_text(post), language=None, wrap_lines=True)


def _posts(repo, sol: dict) -> None:
    if not section("2 · Edit, copy and publish", "pub_posts"):
        _export(repo, sol)
        return
    posts = repo.posts_df(sol["solution_id"])
    if posts.empty:
        st.info("No drafts yet.")
        return
    solution = repo.get_solution(sol["question_id"])
    if (posts["created_at"] < solution["updated_at"]).any():
        st.caption("ℹ️ Some drafts were generated from an earlier version of your solution.")
    records = posts.to_dict("records")
    tabs = st.tabs([f"{PLATFORM_NAMES[p['platform']]} · {p['status']}" for p in records])
    for tab, post in zip(tabs, records):
        with tab:
            _post_editor(repo, post)

    st.divider()
    _export(repo, sol)


def _export(repo, sol: dict) -> None:
    if not section("3 · Export", "pub_export"):
        return
    st.caption("Writes your solution, every post's text and its rendered slide images to data/exports/.")
    if st.button("Export package"):
        folder, data = export_package(repo, sol["solution_id"])
        st.success(f"Saved to `{folder}`")
        st.download_button("Download .zip", data, file_name=f"{folder.name}.zip", mime="application/zip")


def render() -> None:
    repo = get_repo()
    st.title("Publishing Studio")
    st.caption("Your approved solution → Xiaohongshu (中文) and Instagram (English) drafts. "
               "You review everything and publish manually.")
    sol = _pick_solution(repo)
    if not sol:
        return
    question = repo.get_question(sol["question_id"])
    with st.expander("Problem and your solution"):
        st.markdown(normalize_math(question["question_latex"]))
        st.divider()
        st.markdown(normalize_math(repo.get_solution(sol["question_id"])["raw_reasoning"]))
    _generate(repo, sol)
    st.divider()
    _posts(repo, sol)
