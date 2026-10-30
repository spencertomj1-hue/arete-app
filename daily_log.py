"""
Daily log and Habits pages.

Daily log: a month calendar (calendar_component.js/.css) showing each day's
mood, sleep and energy; clicking a day shows its notes, and the form below
saves the entry for the selected day.
Habits: tick habits for a day, see streaks, add/remove habits.
"""

from datetime import date, timedelta
from pathlib import Path

import streamlit as st

import store

HERE = Path(__file__).parent

_calendar_component = st.components.v2.component(
    "log_calendar",
    html='<div class="cal-root"></div>',
    css=(HERE / "calendar_component.css").read_text(),
    js=(HERE / "calendar_component.js").read_text(),
)


def nice_date(d):
    return f"{d:%A} {d.day} {d:%B}"


# ---------------------------------------------------------------------------
# Daily log
# ---------------------------------------------------------------------------

def _on_select():
    picked = (st.session_state["log_calendar"]["select"] or {}).get("date")
    if picked:
        st.session_state["log_day"] = date.fromisoformat(picked)


def _on_month():
    nav = st.session_state["log_calendar"]["nav"] or {}
    if nav.get("dir") == 0:
        st.session_state["log_day"] = date.today()
        st.session_state["log_month"] = date.today().replace(day=1)
        return
    month = st.session_state["log_month"]
    # First day of the previous / next month.
    shifted = (month - timedelta(days=1)) if nav.get("dir", 0) < 0 else (month + timedelta(days=32))
    st.session_state["log_month"] = shifted.replace(day=1)


def page_daily_log():
    st.header("Daily log")
    today = date.today()
    selected = st.session_state.setdefault("log_day", today)
    month = st.session_state.setdefault("log_month", today.replace(day=1))

    # --- Month calendar (6 weeks from the Monday on/before the 1st) ---
    first = month - timedelta(days=month.weekday())
    entries = store.entries_between(first.isoformat(), (first + timedelta(days=41)).isoformat())
    _calendar_component(
        data={
            "month": f"{month:%Y-%m}",
            "today": today.isoformat(),
            "selected": selected.isoformat(),
            "days": {e["date"]: {k: e[k] for k in ("mood", "sleep", "energy", "notes")}
                     for e in entries.to_dict("records")},
        },
        key="log_calendar", on_select_change=_on_select, on_nav_change=_on_month,
    )

    # --- The selected day ---
    d = selected.isoformat()
    entry = store.get_entry(d)
    with st.container(border=True):
        st.subheader(nice_date(selected))
        if entry:
            a, b, c = st.columns(3)
            a.metric("Mood", f"{entry['mood']}/5" if entry.get("mood") else "–")
            b.metric("Sleep", f"{entry['sleep']:g} h" if entry.get("sleep") is not None else "–")
            c.metric("Energy", f"{entry['energy']}/5" if entry.get("energy") else "–")
            st.markdown(entry["notes"] if entry.get("notes") else "*No notes.*")
        else:
            st.caption("Nothing logged for this day yet.")

    # --- Save an entry for the selected day ---
    if selected > today:
        st.info("You can't log a day that hasn't happened yet. Pick today or an earlier day.")
        return
    st.subheader("Save entry")
    # Widget keys include the date so the form reloads when you pick another day.
    with st.form(f"entry_form_{d}"):
        mood = st.slider("Mood", 1, 5, int(entry.get("mood") or 3), key=f"mood_{d}")
        sleep = st.number_input(
            "Sleep (hours)", 0.0, 24.0, float(entry.get("sleep") or 7.0), step=0.5, key=f"sleep_{d}"
        )
        energy = st.slider("Energy", 1, 5, int(entry.get("energy") or 3), key=f"energy_{d}")
        notes = st.text_area("Notes", entry.get("notes") or "", key=f"notes_{d}")
        if st.form_submit_button(f"Save {nice_date(selected)}", type="primary"):
            with store.db() as conn:
                store.save_entry(conn, d, mood, sleep, energy, notes.strip() or None)
            st.toast(f"Saved {nice_date(selected)}.")
            st.rerun()   # refresh the calendar
    if entry:
        st.caption("An entry already exists for this day — saving will update it.")


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

def _toggle_habit(habit_id, day, key):
    """Checkbox callback: save the new tick state straight away."""
    with store.db() as conn:
        store.set_habit_done(conn, habit_id, day, st.session_state[key])


def page_habits():
    st.header("Habits")
    day = st.date_input("Day", value=date.today(), max_value=date.today(), format="DD/MM/YYYY")
    d = day.isoformat()

    habits = store.get_habits()
    if not habits:
        st.info("No habits yet — add one below.")
    for habit_id, name in habits:
        done_dates = store.get_habit_dates(habit_id)
        current, best = store.streaks(done_dates)
        key = f"habit_{habit_id}_{d}"
        left, right = st.columns([3, 2])
        left.checkbox(name, value=d in done_dates, key=key,
                      on_change=_toggle_habit, args=(habit_id, d, key))
        right.caption(f"{current}-day streak · best {best}")

    with st.expander("Manage habits"):
        with st.form("add_habit", clear_on_submit=True):
            new_name = st.text_input("New habit")
            if st.form_submit_button("Add habit") and new_name.strip():
                store.add_habit(new_name.strip())
                st.rerun()

        if habits:
            names = {name: habit_id for habit_id, name in habits}
            to_remove = st.selectbox("Remove a habit", list(names))
            st.caption("Removing a habit also deletes its history.")
            if st.button("Remove habit"):
                store.delete_habit(names[to_remove])
                st.rerun()
