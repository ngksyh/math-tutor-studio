import streamlit as st

from core.config import env_path
from services.ai_provider import (
    AI_MODES,
    PROVIDERS,
    current_mode,
    current_provider,
    list_provider_models,
    provider_base_url,
    provider_model,
)
from services.profile import PROFILE_FIELDS, current_profile
from services.prompting import RULES_VERSION, rules_text, saved_rules_version
from services.secrets import clear_api_key, get_api_key, mask_key, save_api_key
from services.slides import SLIDES_ROOT, current_theme, render_slides
from services.topic_ranker import SIGNAL_LABELS, SIGNALS, current_weights, default_weights
from ui.common import get_repo


PROVIDER_HELP = {
    "openai": "Create a key at platform.openai.com → API keys.",
    "anthropic": "Create a key at platform.claude.com → API keys. Default model: Claude Opus 5.5. Requests "
                 "opt into Anthropic's automatic fallback: if a safety check declines a request, it's re-run on "
                 "the model Anthropic recommends instead of failing.",
    "deepseek": "Create a key at platform.deepseek.com → API keys. Uses DeepSeek's OpenAI-compatible API.",
    "compatible": "Any service with an OpenAI-compatible API (e.g. Qwen, Kimi, OpenRouter): set its base URL, "
                  "key and model name.",
}


def _mode_panel() -> None:
    repo = get_repo()
    st.subheader("AI mode")
    modes = list(AI_MODES)
    mode = st.radio("How should AI work be done?", modes, index=modes.index(current_mode(repo)),
                    format_func=AI_MODES.get)
    if mode != current_mode(repo):
        repo.set_setting("ai_mode", mode)
        st.rerun()
    if mode == "chatgpt_manual":
        st.caption("The app builds a prompt; you paste it into ChatGPT and paste the reply back. "
                   "Uses your ChatGPT Plus subscription — no API key or extra cost.")
        return
    st.caption("The app calls the provider below directly. Usage is billed by that provider, separately "
               "from any ChatGPT/Claude subscription (typically cents per round).")
    providers = list(PROVIDERS)
    provider = st.selectbox("Provider", providers, index=providers.index(current_provider(repo)),
                            format_func=lambda p: PROVIDERS[p].label, key="api_provider_select")
    if provider != current_provider(repo):
        repo.set_setting("api_provider", provider)
        st.rerun()
    if not get_api_key(key_name=PROVIDERS[provider].key_name):
        st.warning(f"No {PROVIDERS[provider].label} key saved yet — add one below, or switch back to ChatGPT mode.")


def _provider_panel(provider: str) -> None:
    repo = get_repo()
    info = PROVIDERS[provider]
    st.markdown(f"**{info.label}**")
    st.caption(PROVIDER_HELP[provider])

    if provider == "compatible":
        with st.form("compatible_url"):
            url = st.text_input("Base URL", value=provider_base_url(repo, provider) or "",
                                placeholder="https://…/v1")
            if st.form_submit_button("Save base URL"):
                repo.set_setting("compatible_base_url", url.strip() or None)
                st.rerun()

    key = get_api_key(key_name=info.key_name)
    st.write(f"Key: `{mask_key(key)}`")
    with st.form(f"key_form_{provider}", clear_on_submit=True):
        new_key = st.text_input("API key", type="password", help="Stored only on this computer, in .env.")
        c1, c2 = st.columns(2)
        save = c1.form_submit_button("Save key", type="primary", width="stretch")
        save_and_test = c2.form_submit_button("Save and test", width="stretch")
    if save or save_and_test:
        try:
            save_api_key(new_key, key_name=info.key_name)
            st.success("Key saved.")
            if save_and_test:
                _test_key(provider)
            else:
                st.rerun()
        except ValueError as e:
            st.error(str(e))
    if key:
        c1, c2 = st.columns(2)
        if c1.button("Test connection", width="stretch", key=f"test_{provider}"):
            _test_key(provider)
        if c2.button("Remove key", width="stretch", key=f"remove_{provider}"):
            clear_api_key(key_name=info.key_name)
            st.session_state.pop(f"models_{provider}", None)
            st.rerun()

    model = provider_model(repo, provider)
    available = st.session_state.get(f"models_{provider}") or list(info.suggested_models)
    if available:
        options = available if model in available else [m for m in [model, *available] if m]
        choice = st.selectbox("Model", options, index=options.index(model) if model in options else 0,
                              accept_new_options=True, key=f"model_{provider}",
                              help="Choose from the list or type a model ID. 'Test connection' loads the "
                                   "models your key can use.")
    else:
        choice = st.text_input("Model ID", value=model, key=f"model_{provider}",
                               help="Press 'Test connection' to load the models your key can use.")
    if choice and choice.strip() != model and st.button("Save model", key=f"save_model_{provider}"):
        repo.set_setting(f"ai_model_{provider}", choice.strip())
        st.rerun()
    st.caption(f"Stored at `{env_path()}` (plain text, git-ignored). Testing only lists models — no tokens used.")


def _test_key(provider: str) -> None:
    info = PROVIDERS[provider]
    with st.spinner(f"Contacting {info.label}…"):
        try:
            models = list_provider_models(get_repo(), provider, get_api_key(key_name=info.key_name) or "")
        except Exception as e:  # noqa: BLE001 — show any connection/auth error to the user
            st.error(f"Connection failed: {type(e).__name__}: {e}")
            return
    st.session_state[f"models_{provider}"] = models
    st.success(f"Connected. {len(models)} models available to this key.")


def _api_panel() -> None:
    repo = get_repo()
    if current_mode(repo) == "api":
        st.subheader("API provider")
        _provider_panel(current_provider(repo))
        return
    with st.expander("API providers (optional — only used in API mode)"):
        providers = list(PROVIDERS)
        provider = st.selectbox("Provider", providers, format_func=lambda p: PROVIDERS[p].label,
                                key="api_provider_browse")
        _provider_panel(provider)


def _chatgpt_project_panel() -> None:
    repo = get_repo()
    saved = saved_rules_version(repo)
    st.subheader("ChatGPT Project (one-time setup)")
    if saved == RULES_VERSION:
        st.success(f"Rules {RULES_VERSION} are saved in your Project — prompts are short.")
    elif saved:
        st.warning(f"Your Project has rules {saved}; the app now uses {RULES_VERSION}. Re-copy the rules below.")
    st.markdown(
        "1. In ChatGPT, create a **Project** (e.g. *Math Tutor Studio*).\n"
        "2. Open the Project's **Instructions** and paste everything below (copy icon at the top-right).\n"
        "3. Always paste prompts from this app into chats **inside that Project**.\n\n"
        "The rules cover all three tasks — questions, math checks and post drafts — so each prompt "
        "then only carries the data. *(A custom GPT with these instructions works too.)*"
    )
    with st.expander("Rules to copy", expanded=saved != RULES_VERSION):
        st.code(rules_text(), language="markdown", height=260, wrap_lines=True)
    in_project = st.checkbox(f"I've saved these rules (version {RULES_VERSION}) in my ChatGPT Project",
                             value=saved == RULES_VERSION)
    if in_project != (saved == RULES_VERSION):
        repo.set_setting("chatgpt_rules_version", RULES_VERSION if in_project else None)
        st.rerun()


def _profile_panel() -> None:
    repo = get_repo()
    st.subheader("Tutor profile")
    st.caption("Drafts may only mention credentials and services written here. Keep it accurate.")
    profile = current_profile(repo)
    with st.form("profile"):
        new = {k: st.text_area(label, value=profile[k], height=110 if "\n" in profile[k] else 68)
               for k, label in PROFILE_FIELDS.items()}
        c1, c2 = st.columns(2)
        if c1.form_submit_button("Save profile", type="primary"):
            repo.set_json_setting("tutor_profile", {k: v.strip() for k, v in new.items()})
            st.success("Saved.")
        if c2.form_submit_button("Reset to config.yaml"):
            repo.set_json_setting("tutor_profile", None)
            st.rerun()


def _slides_panel() -> None:
    repo = get_repo()
    st.subheader("Slide style")
    theme = current_theme(repo)
    with st.form("slide_theme"):
        handle = st.text_input("Handle shown at the bottom of each slide (empty = none)", value=theme.get("handle", ""),
                               placeholder="@yourname")
        c1, c2, c3 = st.columns(3)
        background = c1.color_picker("Background", theme.get("background", "#FAF8F3"))
        text = c2.color_picker("Text", theme.get("text", "#1F2933"))
        accent = c3.color_picker("Accent", theme.get("accent", "#2B5797"))
        b1, b2, b3 = st.columns(3)
        save = b1.form_submit_button("Save style", type="primary")
        preview = b2.form_submit_button("Save and preview")
        reset = b3.form_submit_button("Reset to defaults")
    if reset:
        repo.set_json_setting("slide_theme", None)
        st.rerun()
    if save or preview:
        repo.set_json_setting("slide_theme", {"handle": handle.strip(), "background": background, "text": text,
                                              "accent": accent})
        st.success("Saved.")
    if preview:
        sample = [{"heading": "Why continuity isn't enough",
                   "body": "On $(0,1)$, $f(x)=\\frac{1}{x}$ is continuous but **not** uniformly continuous:\n"
                           "$$\\forall\\delta>0\\ \\exists x,y:\\ |x-y|<\\delta,\\ |f(x)-f(y)|\\ge 1.$$"}]
        with st.spinner("Rendering preview…"):
            result = render_slides(sample, SLIDES_ROOT / "_preview", platform="instagram",
                                   label="MAT137 · Uniform continuity", theme=current_theme(repo))
        if result.error:
            st.error(f"Preview failed: {result.error}")
        else:
            st.image(str(result.paths[0]), width=360)


def _weights_panel() -> None:
    repo = get_repo()
    st.subheader("Topic ranking weights")
    st.caption("Score = 100 × weighted average of the five signals. These are starting assumptions — "
               "adjust them once you see which topics bring saves and inquiries.")
    weights = current_weights(repo)
    with st.form("weights"):
        cols = st.columns(len(SIGNALS))
        new = {k: cols[i].number_input(SIGNAL_LABELS[k], 0.0, 1.0, float(weights[k]), 0.05, key=f"w_{k}")
               for i, k in enumerate(SIGNALS)}
        c1, c2 = st.columns(2)
        if c1.form_submit_button("Save weights", type="primary"):
            if sum(new.values()) <= 0:
                st.error("At least one weight must be above zero.")
            else:
                repo.set_json_setting("ranking_weights", new)
                st.success("Saved. (Weights needn't add up to 1 — they're normalised.)")
        if c2.form_submit_button("Reset to defaults"):
            repo.set_json_setting("ranking_weights", None)
            st.rerun()
    st.caption("Defaults: " + ", ".join(f"{SIGNAL_LABELS[k]} {v:.2f}" for k, v in default_weights().items()))


def _data_panel() -> None:
    repo = get_repo()
    st.subheader("Local data")
    st.write(f"Database: `{repo.db_path}`")
    st.dataframe(
        {"table": list(repo.table_counts()), "rows": list(repo.table_counts().values())},
        hide_index=True,
    )
    if repo.db_path.exists():
        st.download_button("Download database backup", repo.backup_bytes(),
                           file_name="tutoring_backup.db", mime="application/octet-stream",
                           help="A complete single-file copy. To move your data to another computer, put it "
                                "there as data/tutoring.db (with the app closed).")


def render() -> None:
    st.title("Settings")
    _mode_panel()
    st.divider()
    if current_mode(get_repo()) == "chatgpt_manual":
        _chatgpt_project_panel()
        st.divider()
    _profile_panel()
    st.divider()
    _slides_panel()
    st.divider()
    _weights_panel()
    st.divider()
    _api_panel()
    st.divider()
    _data_panel()
