"""Weekly review: when it's due, what it adds up, the pop-up, and the history on Stats."""

import importlib
from datetime import date, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def review(store):
    from arete import review as module
    return importlib.reload(module)


def test_when_its_due(review, store):
    sun = datetime(2026, 11, 1, 18, 59)                       # Sunday 1 Nov, just before 7pm
    assert review.due_week(sun) == date(2026, 10, 19)         # still last week's
    assert review.due_week(sun.replace(hour=19)) == date(2026, 10, 26)
    assert review.due_week(datetime(2026, 11, 3, 9)) == date(2026, 10, 26)   # Tuesday: still that one
    store.set_week_start("Sunday")
    assert review.due_week(datetime(2026, 11, 1, 20)) == date(2026, 10, 25)  # Sun 25 - Sat 31 Oct


def test_summary_numbers(review, store):
    from arete import tools
    tools = importlib.reload(tools)
    tools.add_block("2026-10-27", "plan", "09:00", "11:00", "Revision", category="Study")
    tools.add_block("2026-10-27", "actual", "09:00", "10:00", "Revision", category="Study")
    tools.add_block("2026-10-27", "actual", "10:00", "11:00", "Scrolling", category="Distracted")
    with store.db() as conn:
        store.save_entry(conn, "2026-10-27", 4, 7.0, 3, "Good day", 2.0)
        store.save_entry(conn, "2026-10-20", 2, 6.0, 2, None)    # the week before
    store.add_habit("Gym", per_week=2)
    gym = store.find_habit("Gym")[0]
    with store.db() as conn:
        for d in ("2026-10-26", "2026-10-29"):
            store.set_habit_done(conn, gym, d, True)
    store.add_deadline("Essay", "2026-10-30")
    s = review.summary(date(2026, 10, 26))
    assert (s["planned_hours"], s["actual_hours"], s["followed_pct"]) == (2.0, 2.0, 50)
    assert {t["category"]: (t["plan"], t["actual"]) for t in s["time"]} == {"Study": (2.0, 1.0), "Distracted": (0.0, 1.0)}
    assert s["log"]["mood"] == {"now": 4.0, "before": 2.0} and s["days_logged"] == 1
    assert s["habits"] == [{"name": "Gym", "ticks": 2, "target": 2, "hit": True}]
    assert [d["title"] for d in s["deadlines"]["missed"]] == ["Essay"] and s["has_data"]
    text = review.as_text(s)
    assert "50% of the Plan followed" in text and "Average mood: 4.0 (week before 2.0)" in text


def test_popup_once_and_history(store, monkeypatch):
    from arete import review, tools
    importlib.reload(tools)
    week = store.week_start_of(store.local_today())
    tools.add_block(week.isoformat(), "actual", "09:00", "10:00", "Gym", category="Exercise")
    monkeypatch.setattr(review, "due_week", lambda now=None: week)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception and store.get_review(week).get("seen_at")          # shown, and remembered
    assert any(m.label == "Hours logged" for m in at.metric)
    assert review.should_show() is None
    at = AppTest.from_file(APP, default_timeout=60).run()                      # opened again: not shown again
    assert not any(m.label == "Hours logged" for m in at.metric)
    at.sidebar.radio[0].set_value("Stats").run()
    assert not at.exception and any("Weekly reviews" in s.value for s in at.subheader)


def test_future_plans_dont_count(review, store, monkeypatch):
    from datetime import datetime
    from arete import tools
    tools = importlib.reload(tools)
    tools.add_block("2026-10-27", "plan", "09:00", "10:00", "Revision", category="Study")
    tools.add_block("2026-10-27", "actual", "09:00", "10:00", "Revision", category="Study")
    tools.add_block("2026-10-30", "plan", "09:00", "11:00", "Revision", category="Study")    # Friday: later on
    monkeypatch.setattr(store, "local_now", lambda: datetime(2026, 10, 28, 12, 0))         # it's Wednesday
    s = review.summary(date(2026, 10, 26))
    assert (s["planned_hours"], s["followed_pct"]) == (1.0, 100)
