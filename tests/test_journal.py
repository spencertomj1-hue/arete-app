"""Journal (the Daily Log's notes): store, Claude's tools and the Daily Log page (AppTest)."""

import importlib
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_store(store):
    store.append_journal("2026-10-30", "Good day.")
    assert store.append_journal("2026-10-30", "Gym, then 50% of the problem sheet") == \
        "Good day.\n\nGym, then 50% of the problem sheet"
    store.save_journal(date(2026, 10, 29), "Rainy. Saw Priya.")
    assert [e["date"] for e in store.journal_entries()] == ["2026-10-30", "2026-10-29"]
    assert [e["date"] for e in store.journal_entries("priya")] == ["2026-10-29"]
    assert store.journal_entries("50%") and not store.journal_entries("5_%")      # wildcards are literal
    assert [e["date"] for e in store.journal_entries(last="2026-10-29")] == ["2026-10-29"]
    store.save_journal("2026-10-29", "  ")                                       # blank deletes
    assert store.get_journal("2026-10-29") == ""
    with pytest.raises(ValueError):
        store.append_journal("2026-10-30", " ")


def test_tools(store):
    from arete import tools
    tools = importlib.reload(tools)
    tools.write_journal("Long day in the lab.", date="2026-10-30")
    tools.write_journal("Pizza after.", date="2026-10-30")
    assert store.get_journal("2026-10-30") == "Long day in the lab.\n\nPizza after."
    r = tools.write_journal("Rewritten.", date="2026-10-30", replace=True)
    assert r.pending and "Replace the journal for Fri 30 Oct" in r.pending.summary
    assert store.get_journal("2026-10-30").startswith("Long day")              # not until confirmed
    r.pending.run()
    assert store.get_journal("2026-10-30") == "Rewritten."
    assert "Rewritten." in tools.read_journal(date="2026-10-30").text
    assert tools.read_journal(query="nothing like this").text == "No journal entries found."


def test_journal_is_the_days_notes(store):
    with store.db() as conn:
        store.save_entry(conn, "2026-10-28", 4, 7.0, 3, None, 2.0)
    store.append_journal("2026-10-28", "Lab day.")
    assert store.get_entry("2026-10-28")["notes"] == "Lab day." and store.get_entry("2026-10-28")["mood"] == 4
    store.save_journal("2026-10-28", "")                                  # clears the notes, keeps the numbers
    assert store.get_entry("2026-10-28")["notes"] is None and store.get_entry("2026-10-28")["water"] == 2.0
    store.save_journal("2026-10-27", "Only words")
    store.save_journal("2026-10-27", "")                                  # nothing left: no empty row
    assert store.get_entry("2026-10-27") == {}


def test_old_journal_table_is_folded_into_notes(store):
    with store.db() as conn:
        conn.execute("CREATE TABLE journal (date TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.executemany("INSERT INTO journal VALUES (?, ?, 'x')",
                         [("2026-10-20", "Journal only"), ("2026-10-21", "Pizza after"), ("2026-10-22", "Same")])
        store.save_entry(conn, "2026-10-21", 3, None, None, "Rainy")
        store.save_entry(conn, "2026-10-22", 3, None, None, "Same")
    store.init_db()
    assert store.get_journal("2026-10-20") == "Journal only"
    assert store.get_journal("2026-10-21") == "Rainy\n\nPizza after"
    assert store.get_journal("2026-10-22") == "Same"                      # not doubled
    with store.db() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'journal'").fetchone()
    store.init_db()                                                       # safe to run again


def test_daily_log_journal(store):
    today = store.london_today()
    store.save_journal(today, "Wrote some code.")
    store.save_journal("2026-01-02", "New year, new me")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert "Journal" not in at.sidebar.radio[0].options                    # it lives in the Daily Log now
    at.sidebar.radio[0].set_value("Daily Log").run()
    box = next(t for t in at.text_area if t.label == "Journal")
    assert box.value == "Wrote some code."
    box.set_value("Wrote some code. Then slept.")
    next(b for b in at.button if b.label.startswith("Save ")).click().run()
    assert not at.exception and store.get_journal(today) == "Wrote some code. Then slept."
    at.text_input(key="journal_search").set_value("new year").run()
    assert any("New year, new me" in m.value for m in at.markdown)
