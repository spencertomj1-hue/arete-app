"""Setup wizard and Settings: link checks, connecting, Keychain, Claude status, backups, the wizard flow."""

import importlib
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import MemoryKeyring

APP = str(Path(__file__).resolve().parents[1] / "app.py")
FIXTURE = (Path(__file__).parent / "fixtures" / "sample.ics").read_bytes()
GOOD = "https://outlook.office365.com/published/0000fake/1111fake/calendar.ics"


@pytest.fixture
def outlook(store, tmp_path, monkeypatch):
    from arete import outlook as module
    module = importlib.reload(module)
    monkeypatch.setattr(module, "CACHE", tmp_path / "cache.ics")
    monkeypatch.setattr(module, "keyring", MemoryKeyring())
    return module


# --- link checks ---

def test_good_link(outlook):
    assert outlook.check_link(f"  {GOOD}  ") == (GOOD, None)


def test_html_link_is_swapped(outlook):
    url, note = outlook.check_link(GOOD.replace(".ics", ".html"))
    assert url == GOOD and "switched it to the ICS link" in note


@pytest.mark.parametrize("bad, why", [
    ("", "Paste"),
    ("http://outlook.office365.com/published/x/calendar.ics", "https"),
    ("https://evil.example.com/calendar.ics", "isn't an Outlook calendar link"),
    ("https://outlook.office365.com.evil.com/calendar.ics", "isn't an Outlook calendar link"),
    ("https://outlook.office365.com/published/x/calendar", "end in .ics"),
])
def test_bad_links(outlook, bad, why):
    with pytest.raises(outlook.LinkError, match=why):
        outlook.check_link(bad)


@pytest.mark.parametrize("kind, status, says", [
    ("offline", None, "internet"),
    ("http", 403, "ask your IT team"),
    ("http", 404, "couldn't find that calendar"),
    ("notcal", None, "ICS link"),
])
def test_friendly_errors(outlook, kind, status, says):
    assert says in outlook.friendly(outlook.SyncError("x", kind, status))


def test_try_link_and_connect(outlook, store):
    found = outlook.try_link(GOOD, fetcher=lambda url: FIXTURE)
    assert found["count"] >= 0 and len(found["preview"]) <= 3
    status = outlook.connect(GOOD, found["data"])
    assert status["ok"] and outlook.ics_url() == GOOD                  # saved to the (fake) Keychain
    assert store.outlook_between("2026-10-01", "2026-12-31")
    outlook.disconnect()
    assert outlook.ics_url() is None and store.outlook_between("2026-10-01", "2026-12-31") == []



# --- Claude, settings, backups ---

def test_claude_status(store, tmp_path, monkeypatch):
    from arete import assistant
    from arete import setup_checks
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(assistant, "find_claude", lambda: None)
    assert setup_checks.claude_status()["found"] is False

    fake = tmp_path / "claude"
    fake.write_text('#!/bin/sh\necho \'{"loggedIn": false}\'\n')
    fake.chmod(0o755)
    monkeypatch.setattr(assistant, "find_claude", lambda: str(fake))
    status = setup_checks.claude_status()
    assert status["found"] and not status["logged_in"]

    fake.write_text('#!/bin/sh\necho \'{"loggedIn": true, "email": "me@example.com"}\'\n')
    assert setup_checks.claude_status()["account"] == "me@example.com"


def test_settings_and_backups(store):
    from arete import setup_checks
    setup_checks.save_settings(wizard_done=False, other=1)
    assert setup_checks.load_settings() == {"wizard_done": False, "other": 1}
    path = setup_checks.backup_now()
    assert path.exists() and path.parent == store.DATA_DIR / "backups"
    assert setup_checks.list_backups() == [path]


# --- the wizard ---

def test_wizard_shows_on_a_fresh_data_folder_and_finishes(store, monkeypatch):
    from arete import setup_checks
    os.remove(store.DATA_DIR / "settings.json")                 # a brand-new data folder
    monkeypatch.setattr(setup_checks, "claude_status", lambda: {
        "found": False, "path": None, "logged_in": False, "account": None, "note": "Not installed."})

    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "Welcome to Arete"
    assert not at.sidebar.radio                                  # no app yet

    at.button(key="wiz_next").click().run()                      # -> Claude
    assert any(setup_checks.INSTALL_CMD in c.value for c in at.code)
    at.button(key="wiz_skip").click().run()                      # -> Calendar
    assert any("Publish a calendar" in m.value for m in at.markdown)
    at.button(key="wiz_skip").click().run()                      # -> Microphone
    at.button(key="wiz_next").click().run()                      # -> Done
    assert at.title[0].value == "You're all set"
    at.button(key="wiz_finish").click().run()
    assert not at.exception
    assert setup_checks.load_settings()["wizard_done"] is True
    assert at.sidebar.radio[0].value == "Home"                   # the app

    # Reopening the app later doesn't show it again.
    assert AppTest.from_file(APP, default_timeout=60).run().sidebar.radio
