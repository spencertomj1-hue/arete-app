"""
The actions Claude can take in Arete, as plain Python functions.

assistant.py exposes each one as an MCP tool; the tests call them directly.
They only use store.py, so the rules are the same as the app's pages.

Anything that deletes data, or overwrites/trims existing data, doesn't run
straight away: it returns a Result with a `pending` preview. The chat panel
shows that preview with Confirm / Cancel, and Confirm calls `pending.run()`,
which re-runs the same tool with confirmed=True (so it re-checks the data).
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

import store


class ToolError(Exception):
    """A problem Claude should explain to the user (bad input, unknown name...)."""


@dataclass
class Pending:
    summary: str                   # what will happen, shown next to Confirm / Cancel
    run: Callable[[], "Result"]    # does it


@dataclass
class Result:
    text: str                      # what Claude is told
    changed: bool = False          # data changed (the page should refresh)
    pending: Pending | None = field(default=None)


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def _day(value):
    """Validate an ISO date string; returns it normalised."""
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ToolError(f"'{value}' isn't a date. Use YYYY-MM-DD.")


def nice_day(iso):
    """'2026-10-29' -> 'Thu 29 Oct'."""
    d = date.fromisoformat(iso)
    return f"{d:%a} {d.day} {d:%b}"


def _slot(value, is_end):
    """'HH:mm' on the half hour, within 07:00-01:00 -> slot index."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
    if not m or int(m.group(1)) > 23 or m.group(2) not in ("00", "30"):
        raise ToolError(f"'{value}' isn't a valid time. Use HH:mm on the hour or half hour, e.g. 09:30.")
    t = f"{int(m.group(1)):02d}:{m.group(2)}"
    s = store.slot_of(t)
    lo, hi = (1, store.SLOTS) if is_end else (0, store.SLOTS - 1)
    if not lo <= s <= hi:
        raise ToolError(f"{t} is outside the timetable, which runs from 07:00 to 01:00 (next day).")
    return s


def _track(value):
    v = str(value).strip().lower()
    if v not in store.TRACKS:
        raise ToolError("track must be 'plan' or 'actual'.")
    return v


def _category(label, category):
    cid = store.resolve_category(label=label, category=category)
    if cid is None:
        names = ", ".join(store.load_categories()["name"])
        what = f"category '{category}'" if category else f"default category for '{label}'"
        raise ToolError(f"There's no {what}. Ask the user which category to use: {names}.")
    return cid


def _cat_names():
    cats = store.load_categories()
    return dict(zip(cats["id"], cats["name"]))


def describe_block(b, names=None):
    names = names or _cat_names()
    cat = names.get(b["category_id"], "no category")
    return f"{b['label']} ({cat}) {b['start']}–{b['end']} {b['track']} on {nice_day(b['date'])}"


def _date_range(start_date, end_date, max_days=93):
    first, last = _day(start_date), _day(end_date)
    if last < first:
        first, last = last, first
    if (date.fromisoformat(last) - date.fromisoformat(first)).days >= max_days:
        raise ToolError(f"That range is too long; ask for at most {max_days} days at a time.")
    return first, last


# ---------------------------------------------------------------------------
# Daily log and habits
# ---------------------------------------------------------------------------

def log_day(date, mood=None, sleep=None, energy=None, notes=None, append_notes=False, confirmed=False):
    """Set fields of a day's log; untouched fields keep their values."""
    day = _day(date)
    old = store.get_entry(day)
    new = {k: old.get(k) for k in ("mood", "sleep", "energy", "notes")}
    changes, overwrites = [], []

    for name, value, lo, hi, cast in (("mood", mood, 1, 5, int), ("sleep", sleep, 0, 24, float),
                                      ("energy", energy, 1, 5, int)):
        if value is None:
            continue
        value = cast(value)
        if not lo <= value <= hi:
            raise ToolError(f"{name} must be between {lo} and {hi}.")
        if old.get(name) is not None and old[name] != value:
            overwrites.append(f"{name} {old[name]} → {value}")
        elif old.get(name) != value:
            changes.append(f"{name} {value}")
        new[name] = value

    if notes:
        notes = notes.strip()
        if append_notes and old.get("notes"):
            new["notes"] = f"{old['notes']}\n{notes}"
            changes.append("notes added to")
        elif old.get("notes") and old["notes"] != notes:
            overwrites.append(f"notes replaced (were: “{old['notes']}”)")
            new["notes"] = notes
        else:
            new["notes"] = notes
            changes.append("notes")

    if not changes and not overwrites:
        if any(v is not None for v in (mood, sleep, energy, notes)):
            return Result(f"Nothing to change: {nice_day(day)} already has those values.")
        raise ToolError("Give at least one of mood, sleep, energy or notes.")

    summary = f"{nice_day(day)} log: " + ", ".join(overwrites + changes)
    if overwrites and not confirmed:
        return Result(
            "",
            pending=Pending(summary, lambda: log_day(date, mood, sleep, energy, notes, append_notes, True)),
        )
    with store.db() as conn:
        store.save_entry(conn, day, new["mood"], new["sleep"], new["energy"], new["notes"])
    return Result(f"Saved. {summary}.", changed=True)


def _find_habit(name):
    found = store.find_habit(name)
    if not found:
        names = ", ".join(n for _, n in store.get_habits()) or "none yet"
        raise ToolError(f"There's no habit called '{name}'. Existing habits: {names}. "
                        "Ask the user before adding a new one.")
    return found


def set_habit(date, habit_name, done, confirmed=False):
    day = _day(date)
    habit_id, name = _find_habit(habit_name)
    ticked = day in store.get_habit_dates(habit_id)
    if bool(done) == ticked:
        return Result(f"{name} is already {'ticked' if ticked else 'not ticked'} on {nice_day(day)}.")
    summary = f"{'Tick' if done else 'Untick'} {name} on {nice_day(day)}"
    if not done and not confirmed:
        return Result("", pending=Pending(summary, lambda: set_habit(date, habit_name, done, True)))
    with store.db() as conn:
        store.set_habit_done(conn, habit_id, day, bool(done))
    return Result(f"Done: {summary}.", changed=True)


def add_habit(name):
    name = str(name).strip()
    if not name:
        raise ToolError("The habit needs a name.")
    if store.find_habit(name):
        return Result(f"A habit called '{name}' already exists.")
    store.add_habit(name)
    return Result(f"Added habit '{name}'.", changed=True)


def list_habits(today=None):
    today = today or date.today()
    lines = []
    for habit_id, name in store.get_habits():
        dates = store.get_habit_dates(habit_id)
        current, best = store.streaks(dates, today)
        done_today = "done today" if today.isoformat() in dates else "not done today"
        lines.append(f"- {name}: {done_today}, current streak {current}, best {best}")
    return Result("\n".join(lines) or "No habits yet.")


def query_entries(start_date, end_date):
    first, last = _date_range(start_date, end_date)
    entries = {e["date"]: e for e in store.entries_between(first, last).to_dict("records")}
    ticks = store.habit_ticks_between(first, last)
    lines = []
    for day in sorted(set(entries) | set(ticks)):
        e = entries.get(day, {})
        parts = [f"{k} {e[k]}" for k in ("mood", "sleep", "energy") if e.get(k) is not None]
        if e.get("notes"):
            parts.append(f"notes: “{e['notes']}”")
        if ticks.get(day):
            parts.append("habits: " + ", ".join(ticks[day]))
        lines.append(f"- {day} ({nice_day(day)}): " + "; ".join(parts))
    return Result("\n".join(lines) or f"Nothing logged between {first} and {last}.")


def get_stats(period="30d", today=None):
    """period: '7d', '30d', '90d', 'all', or a number of days."""
    p = str(period).strip().lower()
    if p == "all":
        days = None
    else:
        m = re.fullmatch(r"(\d+)\s*d?", p)
        if not m or int(m.group(1)) < 1:
            raise ToolError("period must be like '7d', '30d', '90d' or 'all'.")
        days = int(m.group(1))
    s = store.get_stats(days, today)
    hours = {track: ", ".join(f"{k} {v:g}h" for k, v in sorted(h.items(), key=lambda kv: -kv[1])) or "none"
             for track, h in s["timetable_hours"].items()}
    habits = ", ".join(f"{k} {v:g}%" for k, v in s["habit_completion_pct"].items()) or "no habits"
    return Result(
        f"{s['start']} to {s['end']} ({s['days']} days, {s['days_logged']} logged)\n"
        f"Averages: mood {s['avg_mood']}, sleep {s['avg_sleep']}h, energy {s['avg_energy']}\n"
        f"Habit completion: {habits}\n"
        f"Timetable plan hours: {hours['plan']}\n"
        f"Timetable actual hours: {hours['actual']}"
    )


# ---------------------------------------------------------------------------
# Timetable
# ---------------------------------------------------------------------------

def list_categories():
    cats = store.load_categories()
    lines = []
    for group in store.groups():
        names = [f"{n} {c}" for n, g, c in zip(cats["name"], cats["grp"], cats["colour"]) if g == group]
        lines.append(f"{group}: " + ", ".join(names))
    names = dict(zip(cats["id"], cats["name"]))
    lm = store.load_label_map()
    lines.append("Default categories for activity labels: " + ", ".join(
        f"{lbl} → {names.get(cid, cid)}" for lbl, cid in zip(lm["label"], lm["category_id"])))
    return Result("\n".join(lines))


OUTLOOK_PREFIX = "outlook:"   # ids Claude sees for Outlook events (query_blocks)


def _outlook_event(block_id):
    """The Outlook event behind an "outlook:<uid>" id, or None for ordinary block ids."""
    if not str(block_id).startswith(OUTLOOK_PREFIX):
        return None
    ev = store.get_outlook(str(block_id)[len(OUTLOOK_PREFIX):])
    if not ev or ev["all_day"]:
        raise ToolError(f"There's no Outlook event with id {block_id} on the timetable. Use query_blocks.")
    return ev


def edit_category(name, colour=None, new_name=None, group=None, create=False):
    """Change a category's colour / name / group, or create a new one (create=True)."""
    cats = store.load_categories()
    match = [r for r in cats.to_dict("records") if r["name"].lower() == str(name).strip().lower()]
    if colour is not None and not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(colour).strip()):
        raise ToolError("colour must be a hex code like #00B0F0.")
    if group is not None:
        group = next((g for g in store.groups() if g.lower() == str(group).strip().lower()), None)
        if group is None:
            raise ToolError(f"group must be one of: {', '.join(store.groups())}.")
    if new_name and new_name.strip().lower() != str(name).strip().lower() and \
            any(r["name"].lower() == new_name.strip().lower() for r in cats.to_dict("records")):
        raise ToolError(f"There's already a category called '{new_name}'.")

    if not match:
        if not create:
            raise ToolError(f"There's no category called '{name}'. Existing: {', '.join(cats['name'])}. "
                            "Set create=true to make a new one (ask the user first).")
        if colour is None:
            raise ToolError("A new category needs a colour (hex, e.g. #00B0F0).")
        cid = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_") or "category"
        group = group or store.groups()[0]
        store.save_category(cid, str(name).strip(), colour.strip(), group)
        return Result(f"Created category {str(name).strip()} ({colour.upper()}) in {group}.",
                      changed=True)

    cat = match[0]
    final_name = new_name.strip() if new_name else cat["name"]
    final_colour = colour.strip() if colour else cat["colour"]
    final_group = group or cat["grp"]
    if (final_name, final_colour.upper(), final_group) == (cat["name"], cat["colour"].upper(), cat["grp"]):
        return Result("Nothing to change.")
    store.save_category(cat["id"], final_name, final_colour, final_group, cat["text_colour"])
    changes = [f"{k} {v}" for k, v, old in (("name", final_name, cat["name"]),
                                            ("colour", final_colour.upper(), cat["colour"].upper()),
                                            ("group", final_group, cat["grp"])) if v != old]
    return Result(f"Updated {cat['name']}: " + ", ".join(changes) + ".", changed=True)


def set_outlook_category(category):
    """Which category Outlook events use (their colour on the timetable)."""
    cid = _category(None, category)
    store.save_settings(outlook_category=cid)
    return Result(f"Outlook events now use {_cat_names()[cid]}.", changed=True)


def set_activity_default(label, category):
    """Make an activity label (e.g. 'Lecture') default to a category."""
    label = str(label).strip()
    if not label:
        raise ToolError("Give the activity label.")
    cid = _category(None, category)
    store.set_label_default(label, cid)
    return Result(f"'{label}' now defaults to {_cat_names()[cid]}.", changed=True)


def add_block(date, track, start, end, label, category=None, details=None, confirmed=False):
    day, track = _day(date), _track(track)
    s, e = _slot(start, False), _slot(end, True)
    if e <= s:
        raise ToolError("end must be after start (the day runs 07:00 → 01:00, so 00:30 is after 23:00).")
    label = str(label).strip()
    if not label:
        raise ToolError("The block needs a label.")
    block = {"label": label, "category_id": _category(label, category), "details": (details or "").strip() or None}
    names = _cat_names()
    what = f"{label} ({names[block['category_id']]}) {store.slot_time(s)}–{store.slot_time(e)} {track} on {nice_day(day)}"

    affected = store.preview_paint(day, track, s, e, block)
    if affected and not confirmed:
        summary = f"Add {what}. This overwrites: " + "; ".join(describe_block(b, names) for b in affected)
        return Result("", pending=Pending(
            summary, lambda: add_block(date, track, start, end, label, category, details, True)))
    store.paint(day, track, s, e, block)
    return Result(f"Added {what}.", changed=True)


def _get_block(block_id):
    b = store.get_block(str(block_id))
    if not b:
        raise ToolError(f"There's no block with id {block_id}. Use query_blocks to find it.")
    return b


def update_block(block_id, label=None, category=None, date=None, track=None, start=None, end=None,
                 details=None, confirmed=False):
    """
    Change a block's label/category/description, or move/resize it. Always previewed
    first. An Outlook event ("outlook:<uid>") becomes the user's own block when changed.
    """
    ev = _outlook_event(block_id)
    if ev:
        old = store._as_block(ev)           # how it will look once it's the user's own block
    else:
        old = _get_block(block_id)
    names = _cat_names()
    new_label = str(label).strip() if label else old["label"]
    if category:
        cid = _category(new_label, category)
    elif label:
        # A new label may imply a new category; keep the old one if there's no default.
        cid = store.resolve_category(label=new_label) or old["category_id"]
    else:
        cid = old["category_id"]

    day = _day(date) if date else old["date"]
    trk = _track(track) if track else old["track"]
    s = _slot(start, False) if start else store.slot_of(old["start"])
    e = _slot(end, True) if end else store.slot_of(old["end"])
    if e <= s:
        raise ToolError("end must be after start.")
    moved = (day, trk, s, e) != (old["date"], old["track"], store.slot_of(old["start"]), store.slot_of(old["end"]))
    new_details = old.get("details") if details is None else (details.strip() or None)
    new = {**old, "date": day, "track": trk, "start": store.slot_time(s), "end": store.slot_time(e),
           "label": new_label, "category_id": cid, "details": new_details}
    if not moved and (new_label, cid, new_details) == (old["label"], old["category_id"], old.get("details")):
        return Result("Nothing to change.")

    block = {"label": new_label, "category_id": cid, "details": new_details}
    summary = f"Change {describe_block(old, names)} → {describe_block(new, names)}"
    if ev:
        summary += " (it becomes your own block; Outlook itself isn't changed)"
    affected = store.preview_paint(day, trk, s, e, block, exclude=old["id"]) if moved else []
    if moved and affected:
        summary += ". This overwrites: " + "; ".join(describe_block(b, names) for b in affected)
    if not confirmed:
        return Result("", pending=Pending(
            summary, lambda: update_block(block_id, label, category, date, track, start, end, details, True)))

    if ev:
        old_id = store.detach_outlook(ev["uid"])
    else:
        old_id = old["id"]
    if moved:
        with store.db() as conn:
            store.apply_block_ops(conn, [("delete", old_id)])
            store.paint(day, trk, s, e, block, conn=conn)
    else:
        store.set_block_label(old_id, new_label, cid, new_details)
    return Result(f"Done: {summary}.", changed=True)


def delete_block(block_id, confirmed=False):
    ev = _outlook_event(block_id)
    if ev:   # an Outlook event: remove it from the timetable only
        what = f"Remove the Outlook event {ev['title']} ({ev['exact']}) on {nice_day(ev['date'])} from the timetable"
        if not confirmed:
            return Result("", pending=Pending(what + " (it stays in Outlook)", lambda: delete_block(block_id, True)))
        store.hide_outlook(ev["uid"])
        return Result(f"Done: {what}.", changed=True)
    b = _get_block(block_id)
    summary = f"Delete {describe_block(b)}"
    if not confirmed:
        return Result("", pending=Pending(summary, lambda: delete_block(block_id, True)))
    store.delete_block(b["id"])
    return Result(f"Done: {summary}.", changed=True)


def query_blocks(start_date, end_date, track=None):
    first, last = _date_range(start_date, end_date)
    trk = _track(track) if track else None
    names = _cat_names()
    rows = store.blocks_between(first, last, trk)
    lines = [
        f"- id {b['id']}: {nice_day(b['date'])} ({b['date']}) {b['track']} {b['start']}–{b['end']} "
        f"{b['label']} ({names.get(b['category_id'], 'no category')})"
        for b in rows
    ]
    if trk in (None, "plan"):
        for ev in store.outlook_between(first, last):
            where = f" at {ev['location']}" if ev["location"] else ""
            if ev["all_day"]:
                lines.append(f"- Outlook all-day: {nice_day(ev['date'])} ({ev['date']}) {ev['title']}{where}")
            else:
                lines.append(f"- id {OUTLOOK_PREFIX}{ev['uid']}: {nice_day(ev['date'])} ({ev['date']}) plan "
                             f"{ev['start']}–{ev['end']} (exactly {ev['exact']}) {ev['title']} (from Outlook){where}")
    return Result("\n".join(lines) or f"No timetable blocks between {first} and {last}.")
