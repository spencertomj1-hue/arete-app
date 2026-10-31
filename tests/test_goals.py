"""Goals in store.py: measurable monthly/yearly targets, habit-linked progress, CSV, and the to-do migration."""

import sqlite3
from datetime import date
from io import StringIO

import pandas as pd


def tick(store, habit_id, *days):
    with store.db() as conn:
        for d in days:
            store.set_habit_done(conn, habit_id, d, True)


def test_manual_progress(store):
    g = store.add_goal("Read books", "yearly", "2026-03-04", 20, "books")
    assert (g["period_start"], g["target"], g["value"], g["habit"]) == ("2026-01-01", 20, 0, None)
    store.add_goal_progress(g["id"], 3)
    store.add_goal_progress(g["id"], -5)                     # never below 0
    assert store.get_goal(g["id"])["value"] == 0
    store.add_goal_progress(g["id"], 2.5)
    store.set_goal_progress(g["id"], 7)
    assert store.get_goal(g["id"])["value"] == 7


def test_habit_linked_progress_counts_ticks_in_the_period(store):
    store.add_habit("Gym")
    hid = store.find_habit("gym")[0]
    g = store.add_goal("Gym", "monthly", "2026-10-15", 12, "sessions", habit_id=hid)
    tick(store, hid, "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-31", "2026-11-01")
    got = store.get_goal(g["id"])
    assert (got["value"], got["habit"]) == (3, "Gym")        # only October's ticks
    store.set_goal_progress(g["id"], 99)                     # the hand-kept number is ignored while linked
    assert store.get_goal(g["id"])["value"] == 3
    store.update_goal(g["id"], habit_id=None)                # unlink: back to the hand-kept number
    assert store.get_goal(g["id"])["value"] == 99


def test_deleting_the_habit_unlinks_the_goal(store):
    store.add_habit("Run")
    hid = store.find_habit("run")[0]
    g = store.add_goal("Run", "monthly", "2026-10-01", 8, habit_id=hid)
    store.delete_habit(hid)
    assert store.get_goal(g["id"])["habit_id"] is None


def test_goals_in_order_and_validation(store):
    a = store.add_goal("A", "monthly", "2026-10-01", 1)
    b = store.add_goal("B", "monthly", "2026-10-20", 2)
    store.reorder_goals([b["id"], a["id"]])
    assert [g["title"] for g in store.goals_in("monthly", "2026-10-31")] == ["B", "A"]
    for bad in (("", "monthly", 1), ("X", "weekly", 1), ("X", "monthly", 0)):
        try:
            store.add_goal(bad[0], bad[1], "2026-10-01", bad[2])
            assert False, bad
        except ValueError:
            pass
    store.update_goal(a["id"], title="A2", target=5, unit="km")
    assert (store.get_goal(a["id"])["title"], store.get_goal(a["id"])["unit"]) == ("A2", "km")


def test_goals_csv_round_trip(store):
    store.add_habit("Gym")
    hid = store.find_habit("gym")[0]
    g = store.add_goal("Gym", "monthly", "2026-10-01", 12, "sessions", habit_id=hid)
    m = store.add_goal("Save", "yearly", "2026-01-01", 1000, "£")
    store.set_goal_progress(m["id"], 250)
    csv = store.load_goals().to_csv(index=False)
    assert store.save_goals(pd.read_csv(StringIO(csv)), replace=True) == 2
    assert store.get_goal(g["id"])["habit"] == "Gym"         # linked again by habit name
    assert store.get_goal(m["id"])["value"] == 250
    rows = pd.DataFrame({"title": ["Swim", "No target", "Bad period"], "period": ["Monthly", "monthly", "weekly"],
                         "target": [4, None, 3], "habit": ["Nope", None, None]})
    assert store.save_goals(rows) == 1                        # unknown habit -> updated by hand
    assert [x["habit"] for x in store.goals_in("monthly", store.local_today()) if x["title"] == "Swim"] == [None]


def test_old_goals_table_becomes_todos(tmp_path, monkeypatch):
    """A database from before the split: its "goals" were to-dos. They move to todos, yearly ones go."""
    import importlib
    data = tmp_path / "old"
    data.mkdir()
    db = data / "life.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE goals (id TEXT PRIMARY KEY, title TEXT NOT NULL, notes TEXT, period TEXT NOT NULL,
                period_start TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, done_at TEXT,
                "repeat" INTEGER NOT NULL DEFAULT 0, ord INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE INDEX goals_period ON goals (period, period_start);
            CREATE TABLE goal_rollover (period TEXT PRIMARY KEY, period_start TEXT NOT NULL);
            INSERT INTO goals VALUES ('d', 'Gym', NULL, 'daily', '2026-10-30', 1, 'x', 0, 0, 'x'),
                                     ('m', 'Budget', NULL, 'monthly', '2026-10-01', 0, NULL, 1, 0, 'x'),
                                     ('y', 'Learn Spanish', NULL, 'yearly', '2026-01-01', 0, NULL, 0, 0, 'x');
            INSERT INTO goal_rollover VALUES ('monthly', '2026-10-01'), ('yearly', '2026-01-01');
        """)
    monkeypatch.setenv("ARETE_DATA_DIR", str(data))
    monkeypatch.setenv("ARETE_DB", str(db))
    from arete import store
    store = importlib.reload(store)
    store.init_db()
    store.init_db()                                           # running again changes nothing
    assert [t["title"] for t in store.todos_for("daily", "2026-10-30")] == ["Gym"]
    assert [t["title"] for t in store.todos_for("monthly", "2026-10-01")] == ["Budget"]
    assert store.get_todo("y") is None                        # yearly to-dos are gone
    assert store.goals_in("yearly", "2026-01-01") == []       # and goals start empty
    backups = list((data / "backups").glob("life-*-before-todos.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:                 # the backup has everything as it was
        assert conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 3
