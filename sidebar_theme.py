"""
The sidebar's look: a marble background (Images/sidebar.webp) behind a narrow
menu with a small box round each item, so the veins never run through the text.

The image is embedded in the CSS as a data URI (read once at import), so no
static file serving is needed.
"""

import base64
from pathlib import Path

import streamlit as st

_IMAGE = base64.b64encode((Path(__file__).parent / "Images" / "sidebar.webp").read_bytes()).decode()


def apply():
    accent = st.get_option("theme.primaryColor") or "#ff4b4b"
    st.html(f"""<style>
    /* The marble, softened by a light wash so it sits further back. */
    [data-testid="stSidebar"] {{
        background:
            linear-gradient(rgba(246, 245, 241, 0.38), rgba(246, 245, 241, 0.38)),
            url("data:image/webp;base64,{_IMAGE}") top center / cover no-repeat,
            #f4f3ef;
    }}
    [data-testid="stSidebar"] > div {{ background: transparent; }}
    [data-testid="stSidebar"] * {{ color: #2b2b30; }}

    /* No "Arete" title: just the menu. */
    [data-testid="stSidebar"] h1 {{ display: none; }}
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(h1) {{ display: none; }}

    /* Narrow: just wider than the longest menu box. */
    [data-testid="stSidebar"] {{ width: 196px !important; min-width: 196px !important; max-width: 196px !important; }}

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
        background: rgba(255, 255, 255, 0.82);
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
        transition: background 0.12s;
    }}
    [data-testid="stSidebar"] [data-testid="stRadioOption"]:hover p {{ background: rgba(255, 255, 255, 0.97); }}
    [data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] p {{
        background: #fff;
        box-shadow: inset 3px 0 0 {accent}, 0 1px 4px rgba(0, 0, 0, 0.1);
    }}
    </style>""")
