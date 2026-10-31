"""
Deadlines: things due on a date, with a countdown.

Home lists the next few (overdue ones in red). The Goals page has the full list:
add, tick off, edit, delete, and "Make time" (Fit time, finishing the day before).
"""

from datetime import timedelta
from html import escape

import streamlit as st

from arete import store

HOME_LIMIT = 5


def _go_to_goals():
    st.session_state["page"] = "Goals"


def _tone(due, today):
    days = (store._as_date(due) - today).days
    return "over" if days < 0 else "soon" if days <= 2 else ""


def render_home():
    today = store.london_today()
    head, link = st.columns([4, 1], vertical_alignment="center")
    head.markdown("**Deadlines**")
    link.button("See all", key="home_deadlines_all", type="tertiary", on_click=_go_to_goals)
    rows = store.deadlines()
    if not rows:
        st.caption("No deadlines. Add them on the Goals page.")
        return
    from arete.ui import appearance
    soon, over = appearance.warn_colours()
    items = "".join(
        f'<div class="dl-row {_tone(d["due"], today)}"><span class="dl-title">{escape(d["title"])}</span>'
        f'<span class="dl-when">{store.countdown(d["due"], today)}</span></div>' for d in rows[:HOME_LIMIT])
    more = f'<div class="dl-more">and {len(rows) - HOME_LIMIT} more</div>' if len(rows) > HOME_LIMIT else ""
    st.html(f"""<style>
      .dl {{ display: grid; gap: 6px; font-size: 0.88rem; }}
      .dl-row {{ display: flex; gap: 8px; align-items: baseline; }}
      .dl-title {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
      .dl-when {{ margin-left: auto; white-space: nowrap; font-variant-numeric: tabular-nums; opacity: 0.8; }}
      .dl-row.soon .dl-when {{ color: {soon}; opacity: 1; font-weight: 600; }}
      .dl-row.over .dl-when {{ color: {over}; opacity: 1; font-weight: 600; }}
      .dl-more {{ opacity: 0.7; font-size: 0.8rem; }}
    </style><div class="dl">{items}{more}</div>""")


@st.dialog("Edit deadline")
def _edit_dialog(did):
    d = store.get_deadline(did)
    if not d:
        st.caption("That deadline is gone.")
        return
    with st.form(f"dl_edit_{did}", border=False):
        title = st.text_input("What's due", d["title"])
        due = st.date_input("Due", store._as_date(d["due"]), format="DD/MM/YYYY")
        notes = st.text_area("Notes", d["notes"] or "", height=80)
        if st.form_submit_button("Save", type="primary"):
            if not title.strip():
                st.error("Say what's due.")
            else:
                store.update_deadline(did, title=title, due=due, notes=notes)
                st.rerun()
    st.divider()
    if st.toggle("Delete this deadline", key=f"dl_del_t_{did}"):
        if st.button("Delete", type="primary", icon=":material/delete:", key=f"dl_del_{did}"):
            store.delete_deadline(did)
            st.rerun()


def _toggle(did, key):
    store.set_deadline_done(did, st.session_state[key])


def _row(d, today):
    tick, title, when, edit, plan = st.columns([0.5, 5, 2, 0.6, 1.6], vertical_alignment="center")
    key = f"dl_done_{d['id']}"
    tick.checkbox("Done", value=d["done"], key=key, label_visibility="collapsed",
                  on_change=_toggle, args=(d["id"], key))
    tone = {"over": ":red", "soon": ":orange"}.get(_tone(d["due"], today), "")
    title.markdown(f"~~{d['title']}~~" if d["done"] else f"**{d['title']}**"
                   + (f"  \n:gray[{d['notes']}]" if d["notes"] else ""))
    due = store._as_date(d["due"])
    text = f"{due:%a} {due.day} {due:%b}" + ("" if d["done"] else f" · {store.countdown(due, today)}")
    when.markdown(f"{tone}[{text}]" if tone and not d["done"] else text)
    if edit.button("", icon=":material/edit:", key=f"dl_edit_btn_{d['id']}", help="Edit or delete"):
        _edit_dialog(d["id"])
    if not d["done"] and due > today and plan.button("Make time", icon=":material/event_available:",
                                                     key=f"dl_plan_{d['id']}", help="Fit time for it into your Plan"):
        from arete.ui import timetable_tools
        timetable_tools.open_fit(d["title"], due - timedelta(days=1))


def render_section():
    today = store.london_today()
    with st.container(border=True):
        rows = store.deadlines(include_done=True)
        open_ = [d for d in rows if not d["done"]]
        st.subheader(f"Deadlines :gray[{len(open_)}]", anchor=False)
        for d in open_:
            _row(d, today)
        if not open_:
            st.caption("Nothing due.")
        with st.form("dl_new", clear_on_submit=True, border=False):
            a, b, c = st.columns([5, 2, 1], vertical_alignment="bottom")
            title = a.text_input("New deadline", placeholder="e.g. Essay, Internship application")
            due = b.date_input("Due", today + timedelta(days=7), format="DD/MM/YYYY")
            if c.form_submit_button("Add", icon=":material/add:") and title.strip():
                store.add_deadline(title, due)
                st.rerun()
        done = [d for d in rows if d["done"]]
        if done:
            with st.expander(f"Done ({len(done)})"):
                for d in reversed(done[-20:]):
                    _row(d, today)
