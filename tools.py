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


# ---------------------------------------------------------------------------
# To-dos: tick-off lists for a day, a week or a month
# ---------------------------------------------------------------------------

PERIOD_NAMES = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}


def _period(value, allowed=store.TODO_PERIODS):
    v = str(value).strip().lower()
    v = {"day": "daily", "today": "daily", "week": "weekly", "month": "monthly", "year": "yearly"}.get(v, v)
    if v not in allowed:
        raise ToolError("period must be " + " or ".join(f"'{a}'" for a in allowed) + ".")
    return v


def _period_day(value):
    return date.fromisoformat(_day(value)) if value else store.london_today()


def todo_list_name(period, start):
    """"this week's to-dos (26 Oct to 1 Nov)", "today's to-dos", "the November 2026 to-dos"."""
    title = store.period_title(period, start)
    named = {"Today": "today's", "Yesterday": "yesterday's", "Tomorrow": "tomorrow's"}.get(title)
    if named:
        return f"{named} to-dos"
    if ": " in title:
        when, span = title.split(": ")
        return f"{when.lower()}'s to-dos ({span})"
    return f"the {title} to-dos"


def describe_todo(t):
    return f"'{t['title']}' in {todo_list_name(t['period'], t['period_start'])}"


def add_todo(title, period, date=None, repeat=False, notes=None):
    """Add a to-do to the period containing `date` (default today)."""
    title, per = str(title).strip(), _period(period)
    if not title:
        raise ToolError("The to-do needs a title.")
    day = _period_day(date)
    store.roll_todos()
    start = store.start_of_period(per, day)
    if any(t["title"].lower() == title.lower() for t in store.todos_for(per, start)):
        return Result(f"'{title}' is already in {todo_list_name(per, start)}.")
    t = store.add_todo(title, per, day, notes=notes, repeat=bool(repeat))
    extra = f", repeating every {PERIOD_NAMES[per]}" if repeat else ""
    return Result(f"Added '{t['title']}' to {todo_list_name(per, start)}{extra}.", changed=True)


def _current_todos(day, period=None):
    """To-dos in the periods containing `day` (all kinds, or just one)."""
    periods = [period] if period else store.TODO_PERIODS
    return [t for p in periods for t in store.todos_for(p, store.start_of_period(p, day))]


FILLER_WORDS = {"the", "a", "an", "my", "to", "do", "finish", "finished", "done", "complete", "tick", "off",
                "goal", "todo"}


def _words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower())) - FILLER_WORDS


def _best_match(pool, text):
    """Items by exact title, then part of a title, then the most shared words
    ("finish problem sheet" -> "Problem sheet 4")."""
    want = str(text).strip().lower()
    exact = [x for x in pool if x["title"].lower() == want]
    part = [x for x in pool if want in x["title"].lower()]
    words = _words(want)
    scores = {x["id"]: len(words & _words(x["title"])) for x in pool}
    best = max(scores.values(), default=0)
    return exact or part or [x for x in pool if best and scores[x["id"]] == best]


def _find_todo(todo, period=None, date=None):
    """A to-do by id, or by title in the periods containing `date` (default today)."""
    store.roll_todos()
    found = store.get_todo(str(todo).strip())
    if found:
        return found
    pool = _current_todos(_period_day(date), _period(period) if period else None)
    matches = _best_match(pool, todo)
    if len(matches) == 1:
        return matches[0]
    if matches:
        options = "; ".join(f"id {t['id']}: {describe_todo(t)}" for t in matches)
        raise ToolError(f"More than one to-do matches '{todo}': {options}. Ask the user which one, or pass its id.")
    raise ToolError(f"There's no to-do matching '{todo}' in the current periods. "
                    "Use list_todos (with a date for other periods) to find it.")


def complete_todo(todo, period=None, date=None):
    t = _find_todo(todo, period, date)
    if t["done"]:
        return Result(f"{describe_todo(t)} is already ticked off.")
    store.set_todo_done(t["id"], True)
    done, total = store.todos_progress(t["period"], t["period_start"])
    return Result(f"Ticked off {describe_todo(t)} ({done}/{total} done).", changed=True)


def uncomplete_todo(todo, period=None, date=None):
    t = _find_todo(todo, period, date)
    if not t["done"]:
        return Result(f"{describe_todo(t)} isn't ticked.")
    store.set_todo_done(t["id"], False)
    return Result(f"Unticked {describe_todo(t)}.", changed=True)


def delete_todo(todo, period=None, date=None, confirmed=False):
    t = _find_todo(todo, period, date)
    summary = f"Delete the to-do {describe_todo(t)}"
    if not confirmed:
        return Result("", pending=Pending(summary, lambda: delete_todo(t["id"], confirmed=True)))
    store.delete_todo(t["id"])
    return Result(f"Done: {summary}.", changed=True)


def list_todos(period=None, date=None):
    """To-dos (with ids) in the periods containing `date` (default today)."""
    store.roll_todos()
    day = _period_day(date)
    sections = []
    for per in ([_period(period)] if period else store.TODO_PERIODS):
        start = store.start_of_period(per, day)
        todos = store.todos_for(per, start)
        done = sum(t["done"] for t in todos)
        name = todo_list_name(per, start)
        lines = [f"{name[0].upper()}{name[1:]}: {done}/{len(todos)} done"]
        for t in todos:
            extras = [x for x in ("repeats" if t["repeat"] else None, f"notes: {t['notes']}" if t["notes"] else None) if x]
            lines.append(f"- [{'x' if t['done'] else ' '}] {t['title']} (id {t['id']}"
                         + "".join(f"; {x}" for x in extras) + ")")
        sections.append("\n".join(lines))
    return Result("\n\n".join(sections))


# ---------------------------------------------------------------------------
# Goals: measurable monthly / yearly targets with progress
# ---------------------------------------------------------------------------

def _amount(goal, n=None):
    """"7/12 sessions" for a goal (or n of its unit)."""
    n = goal["value"] if n is None else n
    return f"{store._num(n)}/{goal['target']}" + (f" {goal['unit']}" if goal["unit"] else "")


def describe_goal(g):
    when = store.period_title(g["period"], g["period_start"])
    how = f", counted from the habit {g['habit']}" if g["habit"] else ""
    return f"'{g['title']}' ({when}: {_amount(g)}{how})"


def _habit_id(name):
    if not name:
        return None
    found = store.find_habit(str(name))
    if not found:
        names = ", ".join(n for _, n in store.get_habits()) or "none yet"
        raise ToolError(f"There's no habit called '{name}'. Existing habits: {names}.")
    return found[0]


def _find_goal(goal, period=None, date=None):
    """A goal by id, or by title in the month and year containing `date` (default today)."""
    found = store.get_goal(str(goal).strip())
    if found:
        return found
    day = _period_day(date)
    periods = [_period(period, store.GOAL_PERIODS)] if period else store.GOAL_PERIODS
    pool = [g for p in periods for g in store.goals_in(p, day)]
    matches = _best_match(pool, goal)
    if len(matches) == 1:
        return matches[0]
    if matches:
        options = "; ".join(f"id {g['id']}: {describe_goal(g)}" for g in matches)
        raise ToolError(f"More than one goal matches '{goal}': {options}. Ask the user which one, or pass its id.")
    raise ToolError(f"There's no goal matching '{goal}' this month or year. Use list_goals to find it.")


def add_goal(title, target, period="monthly", unit=None, habit=None, date=None):
    """A measurable goal for the month or year containing `date` (default today)."""
    per = _period(period, store.GOAL_PERIODS)
    try:
        target = float(target)
    except (TypeError, ValueError):
        raise ToolError("target must be a number, e.g. 12.")
    if target <= 0:
        raise ToolError("target must be more than 0.")
    title = str(title).strip()
    if not title:
        raise ToolError("The goal needs a title.")
    day = _period_day(date)
    if any(g["title"].lower() == title.lower() for g in store.goals_in(per, day)):
        return Result(f"There's already a goal called '{title}' for {store.period_title(per, day)}.")
    g = store.add_goal(title, per, day, target, unit, _habit_id(habit))
    return Result(f"Added the goal {describe_goal(g)}.", changed=True)


def log_goal_progress(goal, amount=1, period=None, date=None):
    """Add to a goal's progress (a negative amount takes some off)."""
    g = _find_goal(goal, period, date)
    if g["habit"]:
        raise ToolError(f"{describe_goal(g)} counts the habit {g['habit']} automatically. "
                        "Tick the habit instead (set_habit).")
    store.add_goal_progress(g["id"], float(amount))
    g = store.get_goal(g["id"])
    return Result(f"Updated {describe_goal(g)}.", changed=True)


def set_goal_progress(goal, value, period=None, date=None):
    """Set a goal's progress to an exact number."""
    g = _find_goal(goal, period, date)
    if g["habit"]:
        raise ToolError(f"{describe_goal(g)} counts the habit {g['habit']} automatically; it can't be set by hand.")
    old = g["value"]
    store.set_goal_progress(g["id"], float(value))
    g = store.get_goal(g["id"])
    return Result(f"Set {describe_goal(g)} (was {store._num(old)}).", changed=True)


def edit_goal(goal, title=None, target=None, unit=None, habit=None, unlink_habit=False, period=None, date=None):
    """Change a goal's title, target, unit, or which habit (if any) it counts."""
    g = _find_goal(goal, period, date)
    changes = {}
    if title:
        changes["title"] = title
    if target is not None:
        if float(target) <= 0:
            raise ToolError("target must be more than 0.")
        changes["target"] = target
    if unit is not None:
        changes["unit"] = unit
    if habit:
        changes["habit_id"] = _habit_id(habit)
    elif unlink_habit:
        changes["habit_id"] = None
    if not changes:
        raise ToolError("Say what to change: title, target, unit, habit or unlink_habit.")
    store.update_goal(g["id"], **changes)
    return Result(f"Updated: {describe_goal(store.get_goal(g['id']))}.", changed=True)


def delete_goal(goal, period=None, date=None, confirmed=False):
    g = _find_goal(goal, period, date)
    summary = f"Delete the goal {describe_goal(g)}"
    if not confirmed:
        return Result("", pending=Pending(summary, lambda: delete_goal(g["id"], confirmed=True)))
    store.delete_goal(g["id"])
    return Result(f"Done: {summary}.", changed=True)


def list_goals(period=None, date=None):
    """Goals (with ids and progress) for the month and year containing `date` (default today)."""
    day = _period_day(date)
    lines = []
    for per in ([_period(period, store.GOAL_PERIODS)] if period else store.GOAL_PERIODS):
        goals = store.goals_in(per, day)
        lines.append(f"{per.capitalize()} goals ({store.period_title(per, day)}):" + ("" if goals else " none"))
        for g in goals:
            pct = round(100 * min(g["value"] / g["target"], 1))
            how = f"; counts habit {g['habit']}" if g["habit"] else ""
            lines.append(f"- {g['title']}: {_amount(g)} ({pct}%) (id {g['id']}{how})")
    return Result("\n".join(lines))
