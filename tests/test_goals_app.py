"""
The Goals page and the Home goal cards (AppTest), and the ops the goal list
component sends. Uses the throwaway data folder from the `store` fixture.
"""

from datetime import date, timedelta
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def app():
    return AppTest.from_file(APP, default_timeout=60).run()


def open_page(at, name):
    radio = at.sidebar.radio[0]
    radio.set_value(next(o for o in radio.options if name in o)).run()
    assert not at.exception, at.exception


def test_goals_page_renders_and_rolls_repeats(store):
    today = store.london_today()
    store.add_goal("Weekly review", "weekly", today - timedelta(weeks=1), repeat=True)
    store.add_goal("Read", "daily", today, notes="20 pages")
    at = app()
    open_page(at, "Goals")
    assert at.header[0].value == "Goals"
    # Opening the app gave the repeating goal a fresh copy this week.
    assert [g["title"] for g in store.goals_for("weekly", today)] == ["Weekly review"]


def test_goals_page_keeps_each_sections_period(store):
    at = app()
    open_page(at, "Goals")
    today = store.london_today()
    assert at.session_state["goals_start_monthly"] == today.replace(day=1)
    at.session_state["goals_start_monthly"] = store.shift_period("monthly", today, -13)
    at.run()
    assert not at.exception


def test_home_cards_render_under_chat(store):
    today = store.london_today()
    for i in range(9):                                   # more than a card shows
        store.add_goal(f"Task {i}", "daily", today)
    store.add_goal("Plan trip", "monthly", today)
    at = app()
    assert not at.exception
    assert at.sidebar.radio[0].value == "Home"


def test_see_all_opens_goals_page(store):
    import goals
    at = app()
    at.session_state["page"] = goals.PAGE                 # what the "See all" callback does
    at.run()
    assert not at.exception
    assert at.header[0].value == "Goals"


def test_settings_has_goals_export(store):
    store.add_goal("Read", "weekly", store.london_today(), notes="a, b")
    at = app()
    open_page(at, "Settings")
    assert "Goals" in [h.value for h in at.subheader]


def test_component_ops(store, monkeypatch):
    import goals
    monkeypatch.setattr(goals.st, "toast", lambda *a, **k: None)
    week = "2026-10-26"
    add = lambda gid, title: goals.apply_op(
        {"kind": "add", "goal": {"id": gid, "title": title, "period": "weekly", "period_start": week}})
    add("g1", "Run")
    add("g1", "Run")                                      # a resent op adds nothing
    add("g2", "Swim")
    goals.apply_op({"kind": "reorder", "ids": ["g2", "g1"]})
    goals.apply_op({"kind": "edit", "id": "g1", "title": "Run 5k"})
    goals.apply_op({"kind": "edit", "id": "g1", "notes": "Park"})
    goals.apply_op({"kind": "edit", "id": "g1", "title": "  "})         # blank titles are ignored
    goals.apply_op({"kind": "repeat", "id": "g1", "repeat": True})
    goals.apply_op({"kind": "toggle", "id": "g2", "done": True})
    assert [(g["id"], g["title"], g["notes"], g["done"], g["repeat"]) for g in store.goals_for("weekly", week)] == [
        ("g2", "Swim", None, True, False), ("g1", "Run 5k", "Park", False, True)]
    goals.apply_op({"kind": "carry", "period": "weekly", "start": week})
    assert [g["title"] for g in store.goals_for("weekly", "2026-11-02")] == ["Run 5k"]
    goals.apply_op({"kind": "move", "id": "g1", "period": "monthly"})
    assert store.get_goal("g1")["period"] == "monthly"
    goals.apply_op({"kind": "delete", "id": "g2"})
    assert store.get_goal("g2") is None
