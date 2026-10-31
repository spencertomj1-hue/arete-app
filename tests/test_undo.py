"""Undo: a snapshot before deletes / overwrites, restored by Undo while nothing else has changed."""

import importlib
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _undo(store):
    from arete import undo
    return importlib.reload(undo)


def test_undo_restores_and_expires(store, monkeypatch):
    undo = _undo(store)
    store.add_habit("Gym")
    undo.checkpoint("Removed the habit 'Gym'")
    store.delete_habit(store.find_habit("Gym")[0])
    assert undo.pending() == "Removed the habit 'Gym'"
    assert undo.restore() == "Removed the habit 'Gym'" and store.find_habit("Gym")
    assert undo.pending() is None                                 # used up

    undo.checkpoint("Removed the habit 'Gym'")
    store.delete_habit(store.find_habit("Gym")[0])
    assert undo.pending()                                         # (the page shows it: that fixes "after")
    store.add_habit("Read")                                       # something else changes...
    assert undo.pending() is None and undo.restore() is None      # ...so Undo would lose it: not offered
    assert store.find_habit("Read") and not store.find_habit("Gym")

    undo.checkpoint("x")
    monkeypatch.setattr(undo.time, "time", lambda: 10 ** 12)      # long after
    assert undo.pending() is None


def test_undo_bar_on_the_page(store):
    g = store.add_goal("Read 20 books", "yearly", store.local_today(), 20)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Goals").run()
    from arete import undo
    undo.checkpoint("Deleted the goal 'Read 20 books'")           # what the Delete button does first
    store.delete_goal(g["id"])
    at.run()
    assert any("Deleted the goal 'Read 20 books'" in m.value for m in at.markdown)
    at.button(key="undo_btn").click().run()
    assert not at.exception and [x["title"] for x in store.goals_in("yearly", store.local_today())] == ["Read 20 books"]
    assert not any("Deleted the goal" in m.value for m in at.markdown)


def test_grid_ops_checkpoint(store):
    from arete import tools, undo
    importlib.reload(tools)
    from arete.ui import timetable
    tools.add_block("2026-10-29", "plan", "09:00", "10:00", "Lecture")
    timetable.apply_grid_op({"kind": "paint", "date": "2026-10-29", "track": "plan", "start": "11:00", "end": "12:00",
                             "block": {"label": "Gym", "categoryId": "exercise"}})
    assert undo.pending() is None                                 # painting on empty time: nothing to undo
    timetable.apply_grid_op({"kind": "paint", "date": "2026-10-29", "track": "plan", "start": "09:00", "end": "10:00",
                             "block": None})
    assert undo.pending() == "Erased Lecture"
    undo.restore()
    assert [b["label"] for b in store.blocks_between("2026-10-29", "2026-10-29")] == ["Lecture", "Gym"]
