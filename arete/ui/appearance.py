"""
Appearance: light, dark, or follow the system (settings.json "theme").

Streamlit keeps the viewer's theme choice in the browser's localStorage
(`stActiveTheme-<path>-v2` = "System" / "Light" / "Dark") and follows the
system when it's "System" or unset. `sync()` makes that match the setting, and
reloads the page once when it changes. Code that draws its own colours asks
`is_dark()` (from st.context.theme, i.e. what's actually showing).
"""

import streamlit as st

from arete import store

MODES = ["System", "Light", "Dark"]
# Text sizes: the page's root font size. Streamlit and the components size text in rem, so it all follows.
SIZES = {"Small": 14, "Normal": 16, "Large": 18, "Extra large": 20}

_sync_component = st.components.v2.component(
    "arete_theme_sync",
    html="<span hidden></span>",
    js="""
export default function ({ data }) {
  const key = `stActiveTheme-${window.location.pathname}-v2`;
  const want = JSON.stringify(data.mode);
  let have = null;
  try { have = window.localStorage.getItem(key); } catch (e) { return; }
  if (have === want || (have === null && data.mode === "System")) return;
  window.localStorage.setItem(key, want);
  window.location.reload();            // Streamlit reads the choice as the page loads
}
""",
)


def mode():
    m = store.load_settings().get("theme", "System")
    return m if m in MODES else "System"


def set_mode(m):
    store.save_settings(theme=m if m in MODES else "System")


def sync():
    _sync_component(data={"mode": mode()}, key="arete_theme_sync")


def is_dark():
    try:
        return st.context.theme.type == "dark"
    except AttributeError:
        return False


def text_size():
    s = store.load_settings().get("text_size", "Normal")
    return s if s in SIZES else "Normal"


def set_text_size(name):
    store.save_settings(text_size=name if name in SIZES else "Normal")


def apply_text_size():
    px = SIZES[text_size()]
    if px != 16:
        st.html(f"<style>html {{ font-size: {px}px !important; }}</style>")


def apply_text_colours():
    """Streamlit's captions are 60% text colour (3.7:1 on white); make them 4.5:1+ in either mode."""
    caption = "#A3A8B8" if is_dark() else "#5F6170"
    st.html(f"""<style>
    [data-testid="stCaptionContainer"] {{ opacity: 1 !important; }}
    [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ color: {caption} !important; }}
    </style>""")


def warn_colours():
    """(soon, overdue) text colours readable on the current background."""
    return ("#fbbf24", "#ff7b72") if is_dark() else ("#b45309", "#c62828")
