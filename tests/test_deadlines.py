"""Deadlines: store, Claude's tools, reminders and the Goals / Home pages (AppTest)."""

import importlib
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_store_and_countdown(store):
    a = store.add_deadline("Essay", "2026-11-06", "2,000 words")
    store.add_deadline("Lab report", "2026-11-02")
    assert [d["title"] for d in store.deadlines()] == ["Lab report", "Essay"]
    store.set_deadline_done(a, True)
    assert [d["title"] for d in store.deadlines()] == ["Lab report"]
    assert len(store.deadlines(include_done=True)) == 2
    store.update_deadline(a, due="2026-11-09", notes="")
    assert store.get_deadline(a)["due"] == "2026-11-09" and store.get_deadline(a)["notes"] is None
    t = date(2026, 11, 2)
    assert [store.countdown(d, t) for d in ("2026-11-02", "2026-11-03", "2026-11-07", "2026-11-01", "2026-10-30")] == \
        ["Today", "Tomorrow", "in 5 days", "1 day overdue", "3 days overdue"]
    with pytest.raises(ValueError):
        store.add_deadline(" ", "2026-11-02")


def test_tools(store, monkeypatch):
    from arete import tools
    tools = importlib.reload(tools)
    monkeypatch.setattr(store, "london_today", lambda: date(2026, 10, 30))
    assert tools.add_deadline("Essay on Kant", "2026-11-06").text == \
        "Added deadline 'Essay on Kant' due Fri 6 Nov (in 7 days)."
    tools.add_deadline("Internship application", "2026-11-15")
    assert tools.complete_deadline("essay").text.startswith("Ticked off 'Essay on Kant'")
    assert "[done]" in tools.list_deadlines(include_done=True).text
    tools.edit_deadline("internship", due="2026-11-14")
    assert store.deadlines()[0]["due"] == "2026-11-14"
    r = tools.delete_deadline("internship")
    assert r.pending and len(store.deadlines()) == 1
    r.pending.run()
    assert store.deadlines() == []


def test_deadline_reminders(store):
    from arete import notify
    notify = importlib.reload(notify)
    store.add_deadline("Essay", "2026-11-03")
    store.add_deadline("Later", "2026-11-10")
    assert notify.due_deadlines(datetime(2026, 11, 2, 8, 0), set()) == []          # before 9 am
    out = notify.due_deadlines(datetime(2026, 11, 2, 9, 5), set())
    assert [(t, b) for _, t, b in out] == [("Due tomorrow: Essay", "")]
    assert notify.due_deadlines(datetime(2026, 11, 2, 9, 5), {out[0][0]}) == []     # once a day


def test_pages(store):
    today = store.london_today()
    store.add_deadline("Essay", today + timedelta(days=3))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("Essay" in h.value and "in 3 days" in h.value for h in at.get("html"))     # Home
    at.sidebar.radio[0].set_value("Goals").run()
    assert not at.exception and any("Deadlines" in s.value for s in at.subheader)
    box = next(t for t in at.text_input if t.label == "New deadline")
    box.set_value("Lab report")
    next(b for b in at.button if b.label == "Add" and "dl_new" in str(b.form_id)).click().run()
    assert [d["title"] for d in store.deadlines()] == ["Essay", "Lab report"]
    at.checkbox(key=f"dl_done_{store.deadlines()[0]['id']}").check().run()
    assert [d["title"] for d in store.deadlines()] == ["Lab report"]
