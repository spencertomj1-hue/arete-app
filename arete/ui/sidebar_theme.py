"""
The sidebar's look: a marble background (assets/sidebar.webp) behind a narrow
menu with a small box round each item, so the veins never run through the text.

The image is embedded in the CSS as a data URI (read once at import), so no
static file serving is needed.
"""

import base64
from pathlib import Path

import streamlit as st

_IMAGE = base64.b64encode((Path(__file__).parents[2] / "assets" / "sidebar.webp").read_bytes()).decode()


# The marble's wash, the menu boxes and the text, for light and dark mode.
LIGHT = {"wash": "rgba(246, 245, 241, 0.38)", "base": "#f4f3ef", "text": "#2b2b30",
         "box": "rgba(255, 255, 255, 0.82)", "hover": "rgba(255, 255, 255, 0.97)", "selected": "#fff"}
DARK = {"wash": "rgba(18, 19, 24, 0.80)", "base": "#15161b", "text": "#ececf1",
        "box": "rgba(38, 39, 48, 0.88)", "hover": "rgba(52, 53, 64, 0.97)", "selected": "#2e2f3a"}


def apply():
    from arete.ui import appearance
    accent = st.get_option("theme.primaryColor") or "#ff4b4b"
    c = DARK if appearance.is_dark() else LIGHT
    st.html(f"""<style>
    /* The marble, softened by a light wash so it sits further back. */
    [data-testid="stSidebar"] {{
        background:
            linear-gradient({c["wash"]}, {c["wash"]}),
            url("data:image/webp;base64,{_IMAGE}") top center / cover no-repeat,
            {c["base"]};
    }}
    [data-testid="stSidebar"] > div {{ background: transparent; }}
    [data-testid="stSidebar"] * {{ color: {c["text"]}; }}

    /* No "Arete" title: just the menu. */
    [data-testid="stSidebar"] h1 {{ display: none; }}
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(h1) {{ display: none; }}

    /* Narrow: just wider than the longest menu box. */
    /* Only while it's open: closed, it takes no room, so the page slides across to fill it. */
    [data-testid="stSidebar"] {{ transition: width 0.3s ease, min-width 0.3s ease, max-width 0.3s ease, transform 0.3s ease; }}
    [data-testid="stSidebar"][data-collapsed="false"] {{
        width: 12.25rem !important; min-width: 12.25rem !important; max-width: 12.25rem !important;   /* grows with the text size */
    }}
    [data-testid="stSidebar"][data-collapsed="true"] {{
        width: 0 !important; min-width: 0 !important; max-width: 0 !important;
    }}

    /* Each bit of text gets its own small box, so the veins never run through it. */
    [data-testid="stSidebar"] [data-testid="stRadioOption"],
    [data-testid="stSidebar"] [data-testid="stRadioOption"]:hover,
    [data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {{
        background: transparent !important;
        box-shadow: none !important;
        padding: 6px 0 !important;
    }}
    [data-testid="stSidebar"] [data-testid="stRadioOption"] p {{
        display: inline-block;
        white-space: nowrap;
        padding: 6px 14px;
        border-radius: 9px;
        background: {c["box"]};
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
        transition: background 0.12s;
    }}
    [data-testid="stSidebar"] [data-testid="stRadioOption"]:hover p {{ background: {c["hover"]}; }}
    [data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] p {{
        background: {c["selected"]};
        box-shadow: inset 3px 0 0 {accent}, 0 1px 4px rgba(0, 0, 0, 0.1);
    }}
    </style>""")
