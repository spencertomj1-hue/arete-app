"""
Goals: period maths, repeating goals, carry over, moves, export/import.
Uses the throwaway database from the `store` fixture.
"""

from datetime import date

import pandas as pd


def test_period_starts(store):
    friday = date(2026, 10, 30)
    assert store.goal_period_start("daily", friday) == friday
    assert store.goal_period_start("weekly", friday) == date(2026, 10, 26)
    assert store.goal_period_start("monthly", friday) == date(2026, 10, 1)
    assert store.goal_period_start("yearly", friday) == date(2026, 1, 1)
    assert store.goal_period_start("weekly", "2026-11-01") == date(2026, 10, 26)   # Sunday ends the week
    assert store.goal_period_start("weekly", "2026-11-02") == date(2026, 11, 2)    # Monday starts one


def test_week_boundaries_across_month_and_year(store):
    assert store.goal_period_start("weekly", "2027-01-01") == date(2026, 12, 28)
    assert store.goal_period_end("weekly", date(2026, 12, 28)) == date(2027, 1, 3)
    assert store.shift_period("weekly", date(2026, 10, 26), 1) == date(2026, 11, 2)
    assert store.shift_period("weekly", date(2026, 10, 26), -1) == date(2026, 10, 19)


def test_month_and_year_rollover(store):
    assert store.shift_period("monthly", date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert store.shift_period("monthly", date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert store.shift_period("monthly", date(2026, 10, 1), 14) == date(2027, 12, 1)
    assert store.shift_period("monthly", "2026-10-17", 1) == date(2026, 11, 1)    # any day of the month
    assert store.goal_period_end("monthly", date(2028, 2, 1)) == date(2028, 2, 29)
    assert store.shift_period("yearly", date(2026, 1, 1), 1) == date(2027, 1, 1)
    assert store.goal_period_end("yearly", date(2026, 1, 1)) == date(2026, 12, 31)
    assert store.shift_period("daily", date(2026, 12, 31), 1) == date(2027, 1, 1)


def test_add_tick_order(store):
    a = store.add_goal("Finish problem sheet", "weekly", "2026-10-30")
    b = store.add_goal("Go gym", "weekly", "2026-10-28")
    assert a["period_start"] == b["period_start"] == "2026-10-26"
    assert [g["title"] for g in store.goals_for("weekly", "2026-11-01")] == ["Finish problem sheet", "Go gym"]

    store.set_goal_done(a["id"], True)
    done_at = store.get_goal(a["id"])["done_at"]
    assert store.get_goal(a["id"])["done"] and done_at
    store.set_goal_done(a["id"], True)                       # ticking again keeps the first time
    assert store.get_goal(a["id"])["done_at"] == done_at
    assert store.goals_progress("weekly", "2026-10-26") == (1, 2)
    store.set_goal_done(a["id"], False)
    assert store.get_goal(a["id"])["done_at"] is None

    store.reorder_goals([b["id"], a["id"]])
    assert [g["title"] for g in store.goals_for("weekly", "2026-10-26")] == ["Go gym", "Finish problem sheet"]


def test_add_is_idempotent_by_id(store):
    store.add_goal("Read", "daily", "2026-10-30", goal_id="g1")
    store.add_goal("Read", "daily", "2026-10-30", goal_id="g1")
    assert len(store.goals_for("daily", "2026-10-30")) == 1


def test_update_and_delete(store):
    g = store.add_goal("Read", "daily", "2026-10-30")
    store.update_goal(g["id"], title="  Read 20 pages ", notes="the novel", repeat=True)
    got = store.get_goal(g["id"])
    assert (got["title"], got["notes"], got["repeat"]) == ("Read 20 pages", "the novel", True)
    store.update_goal(g["id"], notes="  ")
    assert store.get_goal(g["id"])["notes"] is None
    store.delete_goal(g["id"])
    assert store.get_goal(g["id"]) is None


def test_move_to_another_period(store):
    today = date(2026, 10, 30)
    g = store.add_goal("Plan trip", "daily", today)
    assert store.move_goal(g["id"], "monthly", today) == "2026-10-01"
    # Moving from this month back to daily lands on today, not the 1st.
    assert store.move_goal(g["id"], "daily", today) == "2026-10-30"
    # A goal from another period keeps its own dates.
    old = store.add_goal("Old", "weekly", "2026-09-07")
    assert store.move_goal(old["id"], "daily", today) == "2026-09-07"
    assert store.move_goal(old["id"], "yearly", today) == "2026-01-01"


def test_repeating_goals_roll_over_once(store):
    store.add_goal("Go gym", "weekly", "2026-10-26", repeat=True)
    store.add_goal("One-off", "weekly", "2026-10-26")
    store.add_goal("Stretch", "daily", "2026-10-29", repeat=True)
    store.set_goal_done(store.goals_for("weekly", "2026-10-26")[0]["id"], True)

    assert store.roll_goals(date(2026, 10, 30)) == 1          # Stretch for today; this week has its own
    assert [g["title"] for g in store.goals_for("daily", "2026-10-30")] == ["Stretch"]

    assert store.roll_goals(date(2026, 11, 2)) == 2           # a new week and a new day
    week = store.goals_for("weekly", "2026-11-02")
    assert [(g["title"], g["done"], g["repeat"]) for g in week] == [("Go gym", False, True)]

    assert store.roll_goals(date(2026, 11, 2)) == 0           # no duplicates
    store.delete_goal(week[0]["id"])
    assert store.roll_goals(date(2026, 11, 3)) == 1           # only the new day; the deleted copy stays gone
    assert store.goals_for("weekly", "2026-11-02") == []


def test_roll_skips_titles_already_there_and_repeat_off(store):
    store.add_goal("Read", "monthly", "2026-10-01", repeat=True)
    store.add_goal("read", "monthly", "2026-11-01")           # already there (any case)
    store.add_goal("Tidy", "monthly", "2026-09-01", repeat=True)
    tidy = store.add_goal("Tidy", "monthly", "2026-10-01")    # latest copy has repeat off
    assert store.get_goal(tidy["id"])["repeat"] is False
    assert store.roll_goals(date(2026, 11, 15)) == 0
    assert [g["title"] for g in store.goals_for("monthly", "2026-11-01")] == ["read"]


def test_roll_after_a_gap_and_across_the_year(store):
    store.add_goal("Annual review", "yearly", "2026-01-01", repeat=True)
    store.add_goal("Budget", "monthly", "2026-12-01", repeat=True)
    store.roll_goals(date(2027, 1, 20))
    assert [g["title"] for g in store.goals_for("yearly", "2027-01-01")] == ["Annual review"]
    assert [g["title"] for g in store.goals_for("monthly", "2027-01-01")] == ["Budget"]


def test_carry_over(store):
    a = store.add_goal("Essay", "weekly", "2026-10-26", notes="draft 2")
    store.add_goal("Done thing", "weekly", "2026-10-26")
    store.add_goal("Laundry", "weekly", "2026-10-26")
    store.set_goal_done(store.goals_for("weekly", "2026-10-26")[1]["id"], True)
    store.add_goal("laundry", "weekly", "2026-11-02")         # already in next week

    assert store.carry_over("weekly", "2026-10-30") == 1
    nxt = store.goals_for("weekly", "2026-11-02")
    assert [(g["title"], g["notes"], g["done"]) for g in nxt] == [("laundry", None, False), ("Essay", "draft 2", False)]
    assert store.get_goal(a["id"])["period_start"] == "2026-10-26"   # the original stays
    assert store.carry_over("weekly", "2026-10-26") == 0             # carrying twice doubles nothing

    store.add_goal("Year end", "monthly", "2026-12-01")
    assert store.carry_over("monthly", "2026-12-01") == 1
    assert [g["title"] for g in store.goals_for("monthly", "2027-01-01")] == ["Year end"]


def test_export_import_round_trip(store):
    g = store.add_goal("Run 5k", "monthly", "2026-10-05", notes="park", repeat=True)
    store.set_goal_done(g["id"], True)
    store.add_goal("Learn Spanish", "yearly", "2026-03-01")
    csv = store.load_goals().to_csv(index=False)

    store.delete_goal(g["id"])
    from io import StringIO
    assert store.save_goals(pd.read_csv(StringIO(csv))) == 2
    got = store.get_goal(g["id"])
    assert (got["title"], got["notes"], got["period_start"], got["done"], got["repeat"]) == (
        "Run 5k", "park", "2026-10-01", True, True)
    assert len(store.load_goals()) == 2                        # merge updated, didn't double

    # Minimal rows: title + period (+ any date in the period); bad rows are skipped.
    rows = pd.DataFrame({"title": ["Ship it", "", "Bad"], "period": ["Weekly", "daily", "fortnightly"],
                         "period_start": ["2026-10-29", None, None]})
    assert store.save_goals(rows, replace=True) == 1
    assert [(r["title"], r["period_start"]) for r in store.load_goals().to_dict("records")] == [("Ship it", "2026-10-26")]


def test_backups_include_goals(store):
    import sqlite3

    import setup_checks
    store.add_goal("Go gym", "weekly", "2026-10-30")
    path = setup_checks.backup_now()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT title FROM goals").fetchall() == [("Go gym",)]
