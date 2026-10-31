"""Regional settings: week start (and moving weekly to-dos), 12-hour clock, date formats, water units."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def regional(store):
    import importlib
    from arete import regional as module
    return importlib.reload(module)


def test_defaults(regional):
    sat = date(2026, 10, 31)
    assert (regional.week_start(), regional.clock(), regional.date_format(), regional.water_unit()) == \
        ("Monday", "24-hour", "DD/MM/YYYY", "litres")
    assert regional.week_start_of(sat) == date(2026, 10, 26)
    assert (regional.time("13:30"), regional.day_month(sat), regional.numeric_date(sat), regional.water_text(2)) == \
        ("13:30", "Sat 31 Oct", "31/10/2026", "2 L")


def test_american(store, regional):
    store.save_settings(clock="12-hour", date_format="MM/DD/YYYY", water_unit="US fl oz")
    store.set_week_start("Sunday")
    sat = date(2026, 10, 31)
    assert regional.week_start_of(sat) == date(2026, 10, 25) and regional.weekday_names()[0] == "Sun"
    assert [regional.time(t) for t in ("00:30", "09:00", "12:00", "13:30")] == ["12:30 am", "9 am", "12 pm", "1:30 pm"]
    assert regional.times_in("09:05–10:00") == "9:05 am–10 am"
    assert (regional.day_month(sat), regional.long_date(sat), regional.numeric_date(sat)) == \
        ("Sat Oct 31", "Saturday, October 31", "10/31/2026")
    assert regional.water_text(2) == "68 fl oz" and abs(regional.water_in(64) - 1.89) < 0.01
    store.save_settings(date_format="YYYY-MM-DD")
    assert regional.numeric_date(sat) == "2026-10-31"


def test_changing_week_start_moves_weekly_todos(store):
    store.add_todo("Weekly thing", "weekly", "2026-10-28")            # week of Monday 26 Oct
    store.add_todo("Daily thing", "daily", "2026-10-28")
    store.set_week_start("Sunday")
    assert [t["title"] for t in store.todos_for("weekly", "2026-10-25")] == ["Weekly thing"]
    assert [t["title"] for t in store.todos_for("daily", "2026-10-28")] == ["Daily thing"]
    assert store.start_of_period("weekly", "2026-10-31") == date(2026, 10, 25)
    store.set_week_start("Sunday")                                     # no change: nothing moves twice
    store.set_week_start("Monday")
    assert [t["title"] for t in store.todos_for("weekly", "2026-10-26")] == ["Weekly thing"]


def test_weekly_habits_follow_week_start(store):
    dates = {"2026-10-25", "2026-10-26"}                               # a Sunday and the Monday after
    assert store.week_ticks(dates, date(2026, 10, 26)) == 1
    store.set_week_start("Sunday")
    assert store.week_ticks(dates, date(2026, 10, 26)) == 2


def test_region_settings_page(store):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Settings").run()
    at.segmented_control(key="region_clock").set_value("12-hour").run()
    at.segmented_control(key="region_week").set_value("Sunday").run()
    at.selectbox(key="region_date").set_value("MM/DD/YYYY").run()
    at.segmented_control(key="region_water").set_value("US fl oz").run()
    s = store.load_settings()
    assert not at.exception and (s["clock"], s["week_start"], s["date_format"], s["water_unit"]) == \
        ("12-hour", "Sunday", "MM/DD/YYYY", "US fl oz")
    for page in ("Home", "Timetable", "Daily Log", "Stats", "Goals"):
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page
