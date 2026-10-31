"""
Weekly review: the main numbers for one week, worked out from the data already
there (nothing new to log), plus the user's two reflections (store.weekly_reviews).

It's due from 7pm on Sunday: the first time Arete is opened after that, the review
of the week ending that Sunday pops up (ui/review.py). With weeks that start on
Sunday, that's the week that ended the day before. No Streamlit here.
"""

from datetime import timedelta

from arete import store

REVIEW_WEEKDAY = 6          # Sunday
REVIEW_HOUR = 19            # 7pm


def week_for(sunday):
    """The first day of the week a Sunday-evening review covers."""
    first = store.week_start_of(sunday)
    return first - timedelta(days=7) if first == sunday else first      # Sunday-start weeks: the one just ended


def due_week(now=None):
    """The week to review now: the one ending on the latest Sunday that's past 7pm."""
    now = now or store.local_now()
    back = (now.weekday() - REVIEW_WEEKDAY) % 7
    sunday = now.date() - timedelta(days=back)
    if back == 0 and now.hour < REVIEW_HOUR:
        sunday -= timedelta(days=7)
    return week_for(sunday)


def should_show(now=None):
    """The week whose review should pop up now (not seen yet, and with something in it), or None."""
    week = due_week(now)
    if store.get_review(week).get("seen_at"):
        return None
    return week if summary(week)["has_data"] else None      # e.g. a brand-new install: nothing to review


def _avg(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _slot_maps(day):
    """({slot: category} for the Plan, the same for Actual) on one timetable day."""
    plan, actual = {}, {}
    for ev in store.outlook_between(day, day):
        if not ev["all_day"] and ev["start"]:
            for s in range(store.slot_of(ev["start"]), min(store.slot_of(ev["end"]), store.SLOTS)):
                plan[s] = store.outlook_category(ev["title"])
    for b in store.blocks_between(day, day):
        target = plan if b["track"] == "plan" else actual
        for s in range(store.slot_of(b["start"]), min(store.slot_of(b["end"]), store.SLOTS)):
            target[s] = b["category_id"]
    return plan, actual


def _slots_gone(day, now):
    """How many of a timetable day's slots have started by `now` (all of them for a past day)."""
    from datetime import datetime, time
    start = datetime.combine(store._as_date(day), time(store.FIRST_MIN // 60, store.FIRST_MIN % 60))
    return max(0, min(store.SLOTS, int((now - start).total_seconds() // 1800)))


def summary(first):
    """Everything the review shows for the week starting `first` (a date)."""
    first = store._as_date(first)
    last = first + timedelta(days=6)
    days = [(first + timedelta(days=i)).isoformat() for i in range(7)]
    today = store.local_today()

    # --- Time: hours per category, planned and actual; how much of the Plan was followed ---
    cats = store.load_categories()
    names, colours = dict(zip(cats["id"], cats["name"])), dict(zip(cats["id"], cats["colour"]))
    hours, planned, matched = {}, 0, 0
    now = store.local_now()
    for d in days:
        plan, actual = _slot_maps(d)
        # Only time that's already happened counts (for "this week so far").
        cutoff = _slots_gone(d, now)
        plan = {k: v for k, v in plan.items() if k < cutoff}
        actual = {k: v for k, v in actual.items() if k < cutoff}
        for track, slots in (("plan", plan), ("actual", actual)):
            for cat in slots.values():
                hours.setdefault(cat, {"plan": 0.0, "actual": 0.0})[track] += 0.5
        planned += len(plan)
        matched += sum(1 for s, cat in plan.items() if actual.get(s) == cat)
    time = [{"category": names.get(c, "Uncategorised"), "colour": colours.get(c, "#8B8D98"), **h}
            for c, h in sorted(hours.items(), key=lambda kv: -(kv[1]["actual"] + kv[1]["plan"]))]

    # --- Daily log, against the week before ---
    this = store.entries_between(days[0], days[-1]).to_dict("records")
    prev = store.entries_between((first - timedelta(days=7)).isoformat(), (first - timedelta(days=1)).isoformat()).to_dict("records")
    log = {k: {"now": _avg(this, k), "before": _avg(prev, k)} for k in ("mood", "sleep", "energy", "water")}

    # --- Habits ---
    targets, habits = store.habit_targets(), []
    for hid, name in store.get_habits():
        done = store.get_habit_dates(hid)
        ticks = sum(d in done for d in days)
        target = targets.get(hid, 7)
        habits.append({"name": name, "ticks": ticks, "target": target, "hit": ticks >= target})

    # --- To-dos: the week's list, and each day's ---
    weekly = store.todos_for("weekly", first)
    daily = [t for d in days for t in store.todos_for("daily", d)]

    # --- Deadlines that were due this week, and the ones coming up next week ---
    all_deadlines = store.deadlines(include_done=True)
    due = [d for d in all_deadlines if days[0] <= d["due"] <= days[-1]]
    upcoming = [d for d in all_deadlines if not d["done"] and last < store._as_date(d["due"]) <= last + timedelta(days=7)]

    journal = store.journal_entries(first=days[0], last=days[-1])
    people = store.interactions_between(first, last)
    has_data = bool(hours or any(this) or any(h["ticks"] for h in habits) or weekly or daily or journal or people)
    return {
        "first": first, "last": last, "complete": last < today, "has_data": has_data,
        "time": time, "planned_hours": planned / 2, "actual_hours": sum(h["actual"] for h in time),
        "followed_pct": round(100 * matched / planned) if planned else None,
        "log": log, "days_logged": len(this),
        "habits": habits,
        "todos": {"weekly_done": sum(t["done"] for t in weekly), "weekly": len(weekly),
                  "daily_done": sum(t["done"] for t in daily), "daily": len(daily)},
        "deadlines": {"met": [d for d in due if d["done"]], "missed": [d for d in due if not d["done"]],
                      "upcoming": upcoming},
        "journal": journal, "people": people,
        "review": store.get_review(first),
    }


def as_text(s):
    """The summary in a few lines, for Claude."""
    lines = [f"Week {s['first']} to {s['last']}" + ("" if s["complete"] else " (so far)")]
    if s["planned_hours"] or s["actual_hours"]:
        lines.append(f"Time: {s['planned_hours']:g} h planned, {s['actual_hours']:g} h logged as done"
                     + (f", {s['followed_pct']}% of the Plan followed" if s["followed_pct"] is not None else ""))
        lines.append("By category (plan / actual): " + ", ".join(
            f"{t['category']} {t['plan']:g}/{t['actual']:g} h" for t in s["time"]))
    for k, v in s["log"].items():
        if v["now"] is not None:
            lines.append(f"Average {k}: {v['now']}" + (f" (week before {v['before']})" if v["before"] is not None else ""))
    if s["habits"]:
        lines.append("Habits: " + ", ".join(f"{h['name']} {h['ticks']}/{h['target']}" for h in s["habits"]))
    t = s["todos"]
    lines.append(f"To-dos: weekly {t['weekly_done']}/{t['weekly']}, daily {t['daily_done']}/{t['daily']}")
    d = s["deadlines"]
    if d["met"] or d["missed"] or d["upcoming"]:
        lines.append(f"Deadlines: met {[x['title'] for x in d['met']]}, missed {[x['title'] for x in d['missed']]}, "
                     f"next week {[x['title'] + ' ' + x['due'] for x in d['upcoming']]}")
    if s["people"]:
        lines.append("Saw: " + "; ".join(f"{p['person']} ({p['note']})" for p in s["people"]))
    if s["journal"]:
        lines.append(f"Journal entries: {len(s['journal'])}")
    r = s["review"]
    if r.get("went_well") or r.get("to_change"):
        lines.append(f"Their reflections: went well: {r.get('went_well') or '-'}; to change: {r.get('to_change') or '-'}")
    return "\n".join(lines)
