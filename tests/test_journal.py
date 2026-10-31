"""Journal: store, Claude's tools and the page (AppTest), with the throwaway data folder."""

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


def test_page(store):
    today = store.london_today()
    store.save_journal(today, "Wrote some code.")
    store.save_journal("2026-01-02", "New year, new me")
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Journal").run()
    assert not at.exception and at.header[0].value == "Journal"
    assert at.text_area[0].value == "Wrote some code."
    at.text_area[0].set_value("Wrote some code. Then slept.")
    next(b for b in at.button if b.label == "Save").click().run()
    assert not at.exception and store.get_journal(today) == "Wrote some code. Then slept."
    at.text_input(key="journal_search").set_value("new year").run()
    assert any("New year, new me" in m.value for m in at.markdown)
