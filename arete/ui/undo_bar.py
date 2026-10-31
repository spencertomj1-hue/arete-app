"""
The "Undo" bar: after a delete or overwrite, a small bar at the bottom of every
page says what happened, with Undo (arete/undo.py does the work).
"""

import streamlit as st

from arete import undo


def _undo():
    label = undo.restore()
    if label:
        st.toast(f"Undone: {label}", icon=":material/undo:")
    else:
        st.toast("Can't undo that any more: something else has changed since.", icon=":material/info:")


def render():
    label = undo.pending()
    if not label:
        return
    from arete import store
    bottom = 96 if store.ai_enabled() else 24          # above the chat bar when there is one
    st.html(f"""<style>
      .st-key-undo_bar {{ position: fixed; left: 50%; transform: translateX(-50%); bottom: {bottom}px; z-index: 999;
        width: auto !important; max-width: min(560px, 92vw); padding: 6px 8px 6px 16px; border-radius: 12px;
        background: #26272f; box-shadow: 0 6px 24px rgba(0, 0, 0, 0.25); }}
      .st-key-undo_bar p {{ color: #f4f4f6 !important; margin: 0; }}
      .st-key-undo_bar button {{ min-height: 2rem; background: transparent !important;
        border: 1px solid rgba(255, 255, 255, 0.35) !important; color: #fff !important; }}
      .st-key-undo_bar button:hover {{ background: rgba(255, 255, 255, 0.12) !important; }}
      .st-key-undo_bar button:focus-visible {{ outline: 2px solid #fff; outline-offset: 2px; }}
      .st-key-undo_bar button * {{ color: #fff !important; }}
    </style>""")
    with st.container(key="undo_bar", horizontal=True, vertical_alignment="center", gap="small"):
        st.markdown(label)
        st.button("Undo", key="undo_btn", icon=":material/undo:", on_click=_undo)
        st.button("", key="undo_dismiss", icon=":material/close:", on_click=undo.dismiss, help="Dismiss")
