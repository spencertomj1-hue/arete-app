"""
The Home page's to-do lists: today's, this week's and this month's, as three
compact cards. This is the Python side of the to-do list component
(todos_component.js/.css).

The component draws Apple Reminders-style lists and applies edits straight
away; the ops it sends are saved here with store.py and acknowledged in
`acked`, the same pattern as the timetable grid.
"""

from pathlib import Path

import streamlit as st

from arete import store

HERE = Path(__file__).parent

HOME_LIMIT = 6        # to-dos shown per card before "N more"

_todos_component = st.components.v2.component(
    "arete_todos",
    html='<div class="gl-root"></div>',
    css=(HERE / "components" / "todos_component.css").read_text(),
    js=(HERE / "components" / "todos_component.js").read_text(),
)


def apply_op(op):
    """
    Save one op from a to-do list:
      {kind: "add", goal: {id, title, period, period_start}}
      {kind: "toggle", id, done}        {kind: "edit", id, title | notes}
      {kind: "repeat", id, repeat}      {kind: "move", id, period}
      {kind: "delete", id}              {kind: "reorder", ids}
    """
    kind = op["kind"]
    if kind == "add":
        t = op["goal"]
        if t["title"].strip():
            store.add_todo(t["title"], t["period"], t["period_start"], todo_id=t["id"])   # idempotent per id
    elif kind == "toggle":
        store.set_todo_done(op["id"], op["done"])
    elif kind == "edit":
        changes = {k: op[k] for k in ("title", "notes") if k in op}
        if changes.get("title", "x").strip():
            store.update_todo(op["id"], **changes)
    elif kind == "repeat":
        store.update_todo(op["id"], repeat=op["repeat"])
    elif kind == "move":
        store.move_todo(op["id"], op["period"])
    elif kind == "delete":
        store.delete_todo(op["id"])
    elif kind == "reorder":
        store.reorder_todos(op["ids"])


def _on_ops():
    """Save new ops and remember them as acknowledged."""
    ops = st.session_state["home_todos"]["ops"] or []
    acked = st.session_state.setdefault("todo_acked", [])
    new_ops = [op for op in ops if op["opId"] not in acked]
    for op in new_ops:
        apply_op(op)
    # Keep the list short; a list only ever has a few ops in flight.
    st.session_state["todo_acked"] = (acked + [op["opId"] for op in new_ops])[-500:]


def render_home():
    """Today's, this week's and this month's to-dos, side by side (stacked when narrow)."""
    store.roll_todos()
    today = store.london_today()
    sections = []
    for period in store.TODO_PERIODS:
        start = store.start_of_period(period, today)
        sections.append({"period": period, "start": start.isoformat(),
                         "title": store.period_title(period, start, today),
                         "goals": store.todos_for(period, start), "limit": HOME_LIMIT})
    data = {"today": today.isoformat(), "sections": sections, "acked": st.session_state.get("todo_acked", [])}
    _todos_component(data=data, key="home_todos", on_ops_change=_on_ops)
