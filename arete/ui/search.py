"""
Search everything, from Home (bottom of the right-hand column): to-dos, goals,
deadlines, habits, the journal, timetable blocks, Outlook events, people,
groups, times you met someone and weekly reviews. Each result's Open button goes
to the right page (and day / person). The matching is store.search.
"""

from html import escape

import streamlit as st

from arete import regional
from arete import store

ICONS = {"To-do": "check_circle", "Goal": "flag", "Deadline": "event", "Habit": "repeat", "Journal": "edit_note",
         "Timetable": "calendar_view_week", "Outlook": "calendar_month", "Person": "person", "Group": "group",
         "Met": "forum", "Review": "insights"}


def _open(r):
    """Go to where a result lives."""
    kind, ss = r["kind"], st.session_state
    if kind in ("Goal", "Deadline"):
        ss["page"] = "Goals"
        if kind == "Goal":
            ss[f"goals_start_{r['extra']}"] = store._as_date(r["date"])
    elif kind in ("Habit", "Journal"):
        ss["page"] = "Daily Log"
        if r["date"]:
            ss["log_day"] = store._as_date(r["date"])
            ss["log_month"] = ss["log_day"].replace(day=1)
    elif kind in ("Timetable", "Outlook"):
        ss["page"] = "Timetable"
        ss["tt_day"] = store._as_date(r["date"])
    elif kind in ("Person", "Met", "Group"):
        ss["page"] = "Network"
        ss["net_edit"] = {"kind": "group" if kind == "Group" else "person", "id": r["id"]}
    elif kind == "Review":
        ss["page"] = "Stats"
        ss["review_week"] = store._as_date(r["date"])


def _snippet(text, words, width=90):
    """A short bit of text around the first match, with the matches in bold."""
    text = " ".join(str(text or "").split())
    low = text.lower()
    at = min((low.find(w.lower()) for w in words if w.lower() in low), default=0)
    start = max(0, at - 30)
    piece = ("…" if start else "") + text[start:start + width] + ("…" if start + width < len(text) else "")
    out = escape(piece)
    for w in sorted(set(words), key=len, reverse=True):
        lo = out.lower()
        i, parts = 0, []
        while (j := lo.find(escape(w).lower(), i)) >= 0:
            parts += [out[i:j], f"<b>{out[j:j + len(escape(w))]}</b>"]
            i = j + len(escape(w))
        out = "".join(parts) + out[i:]
    return out


def _when(r):
    if not r["date"]:
        return ""
    if r["kind"] == "Review":
        return "week of " + regional.day_month(r["date"], weekday=False)
    when = regional.day_month(r["date"], year=store._as_date(r["date"]).year != store.local_today().year)
    if r["kind"] == "Timetable" and r["extra"]:               # "plan 10:00-12:00" -> "Plan, 10:00–12:00"
        track, _, times = r["extra"].partition(" ")
        start, _, end = times.partition("-")
        when += f", {track.capitalize()} {regional.time_range(start, end)}"
    elif r["kind"] == "Outlook" and r["extra"]:
        when += f", {regional.time(r['extra'])}"
    return when


def render_home():
    st.markdown("**Search**")
    query = st.text_input("Search everything", placeholder="Search to-dos, journal, people, timetable…",
                          label_visibility="collapsed", key="global_search", icon=":material/search:")
    if not query.strip():
        return
    results = store.search(query)
    if not results:
        st.caption(f"Nothing matches “{query.strip()}”.")
        return
    words = query.split()
    st.caption(f"{len(results)} result{'s' * (len(results) != 1)}")
    for i, r in enumerate(results):
        text, detail = (r["detail"] or r["title"], r["title"]) if r["kind"] in ("Met", "Review") else (r["title"], r["detail"])
        if r["kind"] == "Journal":
            text, detail = r["title"], None
        left, right = st.columns([5, 1], vertical_alignment="center")
        left.html(
            f'<div style="font-size:0.88rem;line-height:1.35">'
            f'<span aria-hidden="true" style="font-family:\'Material Symbols Rounded\';font-size:1rem;vertical-align:-3px;opacity:.8">'
            f'{ICONS.get(r["kind"], "search")}</span> '
            f'<span style="opacity:.8">{escape(r["kind"])}{" · " + escape(_when(r)) if _when(r) else ""}'
            f'{" · " + escape(detail) if detail and r["kind"] in ("Met", "Review") else ""}</span><br>'
            f'{_snippet(text, words)}'
            f'{"<br><span style=opacity:.8>" + _snippet(detail, words) + "</span>" if detail and r["kind"] not in ("Met", "Review") else ""}'
            f'</div>')
        if r["kind"] != "To-do":         # to-dos are already here on Home
            right.button("Open", key=f"search_open_{i}_{r['kind']}_{r['id']}", on_click=_open, args=(r,),
                         type="tertiary", icon=":material/arrow_forward:")
