"""Reminders: which blocks are due, the notification command per system, and the Settings tab."""

import importlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def notify(store, monkeypatch):
    from arete import notify as module, system
    module = importlib.reload(module)                       # picks up the reloaded store
    shown = []
    monkeypatch.setattr(system, "notify", lambda title, body="": shown.append((title, body)) or True)
    module.shown = shown
    return module


def paint(store, day, start, end, label, track="plan"):
    store.paint(day, track, store.slot_of(start), store.slot_of(end), {"label": label, "category_id": None})


def test_due_window_and_once_only(notify, store):
    paint(store, "2026-10-30", "10:00", "11:00", "Lecture")
    paint(store, "2026-10-30", "12:00", "13:00", "Lunch")
    paint(store, "2026-10-30", "09:00", "10:00", "Did this", track="actual")   # Actual track: never
    sent = set()
    assert notify.check(sent, datetime(2026, 10, 30, 9, 54)) == []              # 6 min early: not yet
    assert notify.check(sent, datetime(2026, 10, 30, 9, 55, 10)) == \
        [("2026-10-30|10:00|Lecture", "Lecture", "10:00–11:00, in 5 min")]
    assert notify.check(sent, datetime(2026, 10, 30, 9, 58)) == []              # already announced
    assert notify.shown == [("Lecture", "10:00–11:00, in 5 min")]


def test_late_opening_and_after_midnight(notify, store):
    paint(store, "2026-10-30", "10:00", "11:00", "Lecture")
    paint(store, "2026-10-30", "00:30", "01:00", "Bed")              # Friday's timetable, Saturday 00:30
    assert notify.due(datetime(2026, 10, 30, 10, 0, 20), 5, set())[0][2] == "10:00–11:00, now"
    assert notify.due(datetime(2026, 10, 30, 10, 2), 5, set()) == []           # too late
    assert notify.due(datetime(2026, 10, 30, 0, 27), 5, set()) == []           # not Friday 00:30
    assert [d[1] for d in notify.due(datetime(2026, 10, 31, 0, 27), 5, set())] == ["Bed"]


def test_outlook_events_and_settings(notify, store):
    with store.db() as conn:
        conn.execute("INSERT INTO tt_outlook (uid, date, start, \"end\", title, location, all_day, exact) "
                     "VALUES ('a', '2026-10-30', '14:00', '15:00', 'Seminar', 'Room 101', 0, 1), "
                     "('b', '2026-10-30', NULL, NULL, 'Holiday', NULL, 1, 1)")
    assert notify.due(datetime(2026, 10, 30, 13, 50), 10, set()) == \
        [("2026-10-30|14:00|Seminar", "Seminar", "14:00–15:00, in 10 min\nRoom 101")]
    store.save_settings(reminders=False)
    assert notify.check(set(), datetime(2026, 10, 30, 13, 50)) == []
    store.save_settings(reminders=True, reminder_minutes=0)
    assert notify.check(set(), datetime(2026, 10, 30, 13, 50)) == []
    assert len(notify.check(set(), datetime(2026, 10, 30, 14, 0))) == 1


def test_commands(monkeypatch):
    from arete import system
    monkeypatch.setattr(system, "IS_MAC", True)
    argv, env = system.notification_command('Say "hi" & quit', "x")
    assert argv[0] == "osascript" and argv[-2:] == ['Say "hi" & quit', "x"]   # text as arguments, not code
    monkeypatch.setattr(system, "IS_MAC", False)
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    argv, env = system.notification_command("$(evil)", "body")
    assert argv[0] == "powershell.exe" and "$(evil)" not in " ".join(argv) and env["ARETE_TITLE"] == "$(evil)"


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="Windows and macOS only")
def test_real_notification_command_runs():
    """On the CI machines: the real command is accepted by the system."""
    from arete import system
    argv, env = system.notification_command("Arete test", "Ignore me")
    import os
    done = subprocess.run(argv, env={**os.environ, **env}, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_settings_tab(store):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Settings").run()
    at.toggle(key="remind_on").set_value(False).run()
    assert not at.exception and store.load_settings()["reminders"] is False
    at.toggle(key="remind_on").set_value(True).run()
    at.number_input(key="remind_minutes").set_value(10).run()
    assert store.load_settings()["reminder_minutes"] == 10
