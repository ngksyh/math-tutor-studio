"""Math Tutor Studio — run with:  streamlit run app.py"""
import streamlit as st

from core.config import is_demo
from ui import courses, dashboard, library, publisher, questions, research, settings, topics, workspace
from ui.nav import PAGES, with_nav

st.set_page_config(page_title="Math Tutor Studio", page_icon="∑", layout="wide")

# Foldable section headings (ui.common.section) are buttons; style them like subheaders.
st.markdown("""<style>
[class*="st-key-fold_"] button { padding: 0.25rem 0; }
[class*="st-key-fold_"] button p { font-size: 1.45rem; font-weight: 600; color: var(--text-color, inherit); }
[class*="st-key-fold_"] button:hover p { opacity: 0.75; }
</style>""", unsafe_allow_html=True)

PAGES.update({
    "dashboard": st.Page(with_nav("dashboard", dashboard.render), title="Dashboard", icon=":material/home:",
                         url_path="dashboard", default=True),
    "research": st.Page(with_nav("research", research.render), title="Research",
                        icon=":material/travel_explore:", url_path="research"),
    "questions": st.Page(with_nav("questions", questions.render), title="Question Studio",
                         icon=":material/quiz:", url_path="questions"),
    "workspace": st.Page(with_nav("workspace", workspace.render), title="Reasoning Workspace",
                         icon=":material/edit_note:", url_path="workspace"),
    "publishing": st.Page(with_nav("publishing", publisher.render), title="Publishing Studio",
                          icon=":material/campaign:", url_path="publishing"),
    "library": st.Page(library.render, title="Question Library", icon=":material/bar_chart:",
                       url_path="library"),
    "courses": st.Page(courses.render, title="Courses", icon=":material/school:", url_path="courses"),
    "topics": st.Page(topics.render, title="Topics", icon=":material/functions:", url_path="topics"),
    "settings": st.Page(settings.render, title="Settings", icon=":material/settings:", url_path="settings"),
})

if is_demo():
    st.sidebar.info("**Demo mode** — sample data in `data/demo.db`. Your own data isn't used.",
                    icon=":material/science:")

st.navigation({
    "Workflow": [PAGES[k] for k in ("dashboard", "research", "questions", "workspace", "publishing")],
    "Library": [PAGES[k] for k in ("library", "courses", "topics")],
    "System": [PAGES["settings"]],
}).run()
