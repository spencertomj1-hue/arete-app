"""
Journal page: free writing, one entry per day.

Pick a day (today by default), write, Save. A prompt for the day helps if the page
is blank. Below: past entries, newest first, with a search box. Claude can write
here too ("journal: ...") and read it back (tools.write_journal / read_journal).
"""

import hashlib
from datetime import timedelta

import streamlit as st

from arete import store

PAGE = "Journal"

PROMPTS = [
    "What went well today, and why?",
    "What's on your mind right now?",
    "What did you learn today?",
    "What are you grateful for?",
    "What drained your energy today? What gave you energy?",
    "What would make tomorrow a good day?",
    "Who did you enjoy spending time with?",
    "What's something you're putting off, and what's the first small step?",
    "What surprised you today?",
    "How did you look after yourself today?",
    "What's worrying you, and how much of it is in your control?",
    "What are you looking forward to?",
]


def prompt_for(day):
    """The same prompt all day, a different one each day."""
    return PROMPTS[day.toordinal() % len(PROMPTS)]


def nice_date(d):
    return f"{d:%A} {d.day} {d:%B %Y}"


def _go(day):
    st.session_state["journal_day"] = day


def _shift(days):
    st.session_state["journal_day"] = st.session_state["journal_day"] + timedelta(days=days)


def render_page():
    st.header("Journal")
    today = store.london_today()
    day = st.session_state.setdefault("journal_day", today)

    a, b, c, d, _ = st.columns([0.45, 2.2, 0.45, 0.9, 5], vertical_alignment="bottom", gap="small")
    a.button("", icon=":material/chevron_left:", key="journal_prev", on_click=_shift, args=(-1,), help="Day before")
    b.date_input("Day", key="journal_day", format="DD/MM/YYYY", max_value=today)
    c.button("", icon=":material/chevron_right:", key="journal_next", on_click=_shift, args=(1,),
             disabled=day >= today, help="Day after")
    d.button("Today", key="journal_today", on_click=_go, args=(today,), disabled=day == today)

    saved = store.get_journal(day)
    # Keyed on the saved text, so the box shows Claude's additions instead of a stale draft.
    version = hashlib.sha1(saved.encode("utf-8")).hexdigest()[:8]
    with st.form(f"journal_form_{day}_{version}", border=False):
        text = st.text_area(nice_date(day), value=saved, height=320, placeholder=prompt_for(day),
                            help="Saved to this computer only.")
        left, right = st.columns([1, 9], vertical_alignment="center")
        if left.form_submit_button("Save", type="primary", icon=":material/save:"):
            store.save_journal(day, text)
            st.toast("Saved" if text.strip() else "Entry deleted", icon=":material/check_circle:")
            st.rerun()
        right.caption(f"{len(saved.split())} words" if saved else f"Prompt: {prompt_for(day)}")

    st.subheader("Past entries", anchor=False)
    query = st.text_input("Search", placeholder="Search your journal", label_visibility="collapsed",
                          key="journal_search")
    entries = store.journal_entries(query or None, limit=None if query else 30)
    if not entries:
        st.caption("No matches." if query else "Nothing written yet.")
    for e in entries:
        d = store._as_date(e["date"])
        first_line = e["text"].strip().splitlines()[0]
        preview = first_line[:70] + ("…" if len(first_line) > 70 else "")
        with st.expander(f"**{d:%a} {d.day} {d:%b %Y}** · {preview}", expanded=bool(query) and len(entries) <= 3):
            st.markdown(e["text"].replace("\n", "  \n"))
            st.button("Open this day", key=f"journal_open_{e['date']}", on_click=_go, args=(d,),
                      icon=":material/edit:")
