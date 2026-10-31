"""
Timetable page: a week at a time, Plan vs Actual per day in 30-minute slots,
coloured by the same category key as the old Excel timetable.

The week grid is a small custom Streamlit component (timetable_component.js/.css).
It paints locally for instant feedback and sends edit operations back here.
What gets saved is computed by store.plan_paint, the same rules the Claude
assistant's add_block tool uses.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

from arete import outlook
from arete import regional
from arete import store

HERE = Path(__file__).parent
SYNC_EVERY = timedelta(minutes=15)

# The grid component, built once from the JS/CSS files next to this module.
_timetable_component = st.components.v2.component(
    "daily_timetable",
    html='<div class="tt-root"></div>',
    css=(HERE / "components" / "timetable_component.css").read_text(encoding="utf-8"),
    js=(HERE / "components" / "timetable_component.js").read_text(encoding="utf-8"),
)


# ---------------------------------------------------------------------------
# Grid callbacks
# ---------------------------------------------------------------------------

def _overlapping(date, track, s, e):
    """Labels of the blocks (and, in the Plan, Outlook events) a paint would cover."""
    labels = [b["label"] for b in store.blocks_between(date, date, track)
              if store.slot_of(b["start"]) < e and store.slot_of(b["end"]) > s]
    if track == "plan":
        labels += [ev["title"] for ev in store.outlook_between(date, date)
                   if not ev["all_day"] and ev["start"] and store.slot_of(ev["start"]) < e and store.slot_of(ev["end"]) > s]
    return labels


def _checkpoint(op):
    """Before a grid change that deletes or overwrites something, save an Undo point for it."""
    from arete import undo
    kind = op["kind"]
    if kind == "paint":
        s, e = store.slot_of(op["start"]), store.slot_of(op["end"])
        covered = _overlapping(op["date"], op["track"], s, e)
        if covered:
            what = ", ".join(dict.fromkeys(covered))
            undo.checkpoint(f"Erased {what}" if not op.get("block") else f"Painted over {what}")
    elif kind == "delete":
        b = store.get_block(op["id"])
        undo.checkpoint(f"Deleted {b['label']}" if b else "Deleted a block")
    elif kind == "upsert":
        undo.checkpoint(f"Edited {op['entry'].get('label') or 'a block'}")
    elif kind == "hide_outlook":
        undo.checkpoint("Removed an Outlook event from the timetable")


def apply_grid_op(op):
    """
    Save one op from the grid:
      {kind: "paint", date, track, start, end, block: {label, categoryId} | null, newId}
      {kind: "upsert", entry}   (edits from the block card / right-click)
      {kind: "delete", id}
      {kind: "hide_outlook", uid}     remove an Outlook occurrence from the timetable
      {kind: "detach_outlook", uid}   turn it into the user's own block (id "ol:<uid>")
    Painting over an Outlook event detaches it first (store.paint), so it's trimmed like any block.
    """
    _checkpoint(op)
    if op["kind"] == "paint":
        s, e = store.slot_of(op["start"]), store.slot_of(op["end"])
        block = op.get("block")
        if block:
            block = {"label": block["label"], "category_id": block["categoryId"]}
        store.paint(op["date"], op["track"], s, e, block, new_id=op.get("newId"))
    elif op["kind"] == "upsert":
        e = op["entry"]
        store.upsert_blocks([{**{k: e[k] for k in ("id", "date", "track", "start", "end", "label")},
                              "category_id": e["categoryId"], "details": e.get("details") or None}])
    elif op["kind"] == "delete":
        store.delete_block(op["id"])
    elif op["kind"] == "hide_outlook":
        store.hide_outlook(op["uid"])
    elif op["kind"] == "detach_outlook":
        store.detach_outlook(op["uid"])


def _on_ops(key):
    """Component callback: save new ops and remember them as acknowledged."""
    ops = st.session_state[key]["ops"] or []
    acked = st.session_state.setdefault("tt_acked", [])
    new_ops = [op for op in ops if op["opId"] not in acked]
    for op in new_ops:
        apply_grid_op(op)
    # Keep the list short; the grid only ever has a few ops in flight.
    st.session_state["tt_acked"] = (acked + [op["opId"] for op in new_ops])[-500:]


def _shift_week(weeks):
    """Prev/next/this-week callback (also used by sideways swipes on the grid)."""
    if weeks == 0:
        st.session_state["tt_day"] = store.local_today()
    else:
        st.session_state["tt_day"] += timedelta(weeks=weeks)


def _on_nav(key):
    """Component callback: ‹ / Today / › buttons, the date box, or a sideways swipe."""
    nav = st.session_state[key]["nav"]
    if not nav:
        return
    if nav.get("date"):
        st.session_state["tt_day"] = date.fromisoformat(nav["date"])
    else:
        _shift_week(nav["dir"])


def sync_outlook(force=False):
    """
    Sync the Outlook calendar if forced or the last try was over 15 minutes ago.
    Returns True only if the imported events changed (so the page needs redrawing).
    """
    now = datetime.now()
    last = st.session_state.get("outlook_tried")
    # (a little slack so the 15-minute timer never just misses the threshold)
    if force or last is None or now - last >= SYNC_EVERY - timedelta(seconds=30):
        st.session_state["outlook_tried"] = now
        before = store.outlook_signature()
        st.session_state["outlook_status"] = outlook.sync()
        return store.outlook_signature() != before
    return False


def _on_sync(key):
    sync_outlook(force=True)


def _on_outlook_toggle(key):
    value = st.session_state[key]["outlook"] or {}
    st.session_state["tt_show_outlook"] = bool(value.get("show"))


def _sync_text():
    """'Synced 5 min ago', or an offline note. Never mentions the calendar URL."""
    if not outlook.ics_url():
        return {"text": "Outlook not connected", "error": False,
                "detail": "Connect your Outlook calendar in Settings → Calendar."}
    status = st.session_state.get("outlook_status") or {}
    when = outlook.ago(status.get("synced_at") or outlook.last_synced())
    if status.get("error"):
        return {"text": f"Offline · events from {when}", "error": True,
                "detail": f"Outlook sync failed: {status['error']} Showing the last synced events."}
    return {"text": f"Synced {when}", "error": False, "detail": "Outlook calendar (read only)"}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render_page():
    day = st.session_state.setdefault("tt_day", store.local_today())
    monday = store.week_start_of(day)            # the week's first day: Monday, or Sunday if chosen
    # Add block / Fit time / Categories: the same things Claude can do, by hand.
    from arete.ui import timetable_tools
    timetable_tools.toolbar(day)
    render_grid([monday + timedelta(days=i) for i in range(7)])


def render_today():
    """Just today's Plan / Actual (the Home page): same grid, view only, no week navigation."""
    render_grid([store.local_today()], key="timetable_today", single=True, readonly=True)


def render_grid(days, key="timetable", single=False, readonly=False):
    """Draw the grid for the given days; returns the categories and label map it used."""
    categories = store.load_categories()
    entries = store.blocks_between(days[0].isoformat(), days[-1].isoformat())
    label_map = store.load_label_map()
    show_outlook = st.session_state.setdefault("tt_show_outlook", True)
    events = store.outlook_between(days[0].isoformat(), days[-1].isoformat()) if show_outlook else []

    data = {
        "days": [d.isoformat() for d in days],
        "today": store.local_today().isoformat(),
        "groups": store.groups(),
        "categories": [
            {"id": r.id, "name": r.name, "colour": r.colour, "textColour": r.text_colour,
             "group": r.grp, "order": int(r.ord)}
            for r in categories.itertuples()
        ],
        "entries": [
            {"id": e["id"], "date": e["date"], "track": e["track"], "start": e["start"], "end": e["end"],
             "label": e["label"], "categoryId": e["category_id"], "details": e["details"]}
            for e in entries
        ],
        "labelMap": dict(zip(label_map["label"], label_map["category_id"])),
        "acked": st.session_state.get("tt_acked", []),
        "single": single,
        "readonly": readonly,
        # Outlook events (Plan; all-day ones as header chips). The user can remove or change them.
        "showOutlook": show_outlook,
        "outlook": [
            {"id": e["uid"], "date": e["date"], "start": e["start"], "end": e["end"], "title": e["title"],
             "location": e["location"], "exact": regional.times_in(e["exact"]), "description": e["description"],
             "details": store.outlook_details(e), "categoryId": store.outlook_category(e["title"])}
            for e in events if not e["all_day"]
        ],
        "outlookAllDay": [{"date": e["date"], "title": e["title"]} for e in events if e["all_day"]]
        + [{"date": d["due"], "title": f"Due: {d['title']}", "deadline": True} for d in store.deadlines()
           if days[0].isoformat() <= d["due"] <= days[-1].isoformat()],
        "sync": _sync_text(),
        "clock": regional.clock(),
        "slots": store.SLOTS,
        "firstMin": store.FIRST_MIN,
        "dateFormat": regional.date_format(),
    }
    _timetable_component(data=data, key=key,
                         on_ops_change=lambda: _on_ops(key), on_nav_change=lambda: _on_nav(key),
                         on_sync_change=lambda: _on_sync(key), on_outlook_change=lambda: _on_outlook_toggle(key))
    return categories, label_map

