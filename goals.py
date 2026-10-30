"""
Goals page and the Home page's goal cards: the Python side of the goal list
component (goals_component.js/.css).

The component draws Apple Reminders-style lists and applies edits straight
away; the ops it sends are saved here with store.py and acknowledged in
`acked`, the same pattern as the timetable grid.
"""

from pathlib import Path

import streamlit as st

import store

HERE = Path(__file__).parent

PAGE = "Goals"                               # sidebar label (also what "See all" switches to)
NAMES = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "yearly": "Yearly"}
HOME_PERIODS = ("daily", "weekly", "monthly")
HOME_LIMIT = 6                                # goals shown per Home card before "N more"

_goals_component = st.components.v2.component(
    "arete_goals",
    html='<div class="gl-root"></div>',
    css=(HERE / "goals_component.css").read_text(),
    js=(HERE / "goals_component.js").read_text(),
)


# ---------------------------------------------------------------------------
# Component callbacks
# ---------------------------------------------------------------------------

def apply_op(op):
    """
    Save one op from a goal list:
      {kind: "add", goal: {id, title, period, period_start}}
      {kind: "toggle", id, done}        {kind: "edit", id, title | notes}
      {kind: "repeat", id, repeat}      {kind: "move", id, period}
      {kind: "delete", id}              {kind: "reorder", ids}
      {kind: "carry", period, start}
    """
    kind = op["kind"]
    if kind == "add":
        g = op["goal"]
        if g["title"].strip():
            store.add_goal(g["title"], g["period"], g["period_start"], goal_id=g["id"])   # idempotent per id
    elif kind == "toggle":
        store.set_goal_done(op["id"], op["done"])
    elif kind == "edit":
        changes = {k: op[k] for k in ("title", "notes") if k in op}
        if changes.get("title", "x").strip():
            store.update_goal(op["id"], **changes)
    elif kind == "repeat":
        store.update_goal(op["id"], repeat=op["repeat"])
    elif kind == "move":
        store.move_goal(op["id"], op["period"])
    elif kind == "delete":
        store.delete_goal(op["id"])
    elif kind == "reorder":
        store.reorder_goals(op["ids"])
    elif kind == "carry":
        copied = store.carry_over(op["period"], op["start"])
        nxt = store.period_title(op["period"], store.shift_period(op["period"], op["start"], 1))
        st.toast(f"Copied {copied} unfinished goal{'s' if copied != 1 else ''} to {nxt}." if copied
                 else f"{nxt} already has those goals.", icon=":material/arrow_forward:")


def _on_ops(key):
    """Save new ops and remember them as acknowledged."""
    ops = st.session_state[key]["ops"] or []
    acked = st.session_state.setdefault("goal_acked", [])
    new_ops = [op for op in ops if op["opId"] not in acked]
    for op in new_ops:
        apply_op(op)
    # Keep the list short; a list only ever has a few ops in flight.
    st.session_state["goal_acked"] = (acked + [op["opId"] for op in new_ops])[-500:]


def _start_key(period):
    return f"goals_start_{period}"


def _on_nav(key):
    """‹ / › / back to the current period, for one section of the Goals page."""
    nav = st.session_state[key]["nav"]
    if not nav:
        return
    period, sk = nav["period"], _start_key(nav["period"])
    if nav["dir"] == 0:
        st.session_state[sk] = store.goal_period_start(period, store.london_today())
    else:
        st.session_state[sk] = store.shift_period(period, st.session_state[sk], nav["dir"])


def _on_see_all():
    st.session_state["page"] = PAGE   # the sidebar radio's key; callbacks run before it's drawn


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _section(period, start, today, limit=None):
    return {
        "period": period,
        "name": NAMES[period],
        "start": start.isoformat(),
        "title": store.period_title(period, start, today),
        "current": start == store.goal_period_start(period, today),
        "goals": store.goals_for(period, start),
        "limit": limit,
    }


def _draw(sections, compact, key, today):
    data = {
        "today": today.isoformat(),
        "compact": compact,
        "sections": sections,
        "acked": st.session_state.get("goal_acked", []),
    }
    _goals_component(data=data, key=key, on_ops_change=lambda: _on_ops(key),
                     on_nav_change=lambda: _on_nav(key), on_seeAll_change=_on_see_all)


def render_home():
    """Daily, weekly and monthly goals for now, as three compact cards (stacked when narrow)."""
    store.roll_goals()
    today = store.london_today()
    sections = [_section(p, store.goal_period_start(p, today), today, HOME_LIMIT) for p in HOME_PERIODS]
    _draw(sections, compact=True, key="home_goals", today=today)


def render_page():
    st.header("Goals")
    store.roll_goals()
    today = store.london_today()
    sections = []
    for period in store.GOAL_PERIODS:
        start = st.session_state.setdefault(_start_key(period), store.goal_period_start(period, today))
        sections.append(_section(period, start, today))
    _draw(sections, compact=False, key="goals_page", today=today)
