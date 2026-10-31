"""Setup wizard and Settings: link checks, connecting, Keychain, Claude status, backups, the wizard flow."""

import importlib
import json
import os
import subprocess
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

    # A found CLI; what `claude auth status --json` prints is faked, so this works on every OS.
    answer = {}

    def fake_run(args, **kwargs):
        assert args[1:] == ["auth", "status", "--json"]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(answer), stderr="")

    monkeypatch.setattr(assistant, "find_claude", lambda: str(tmp_path / "claude"))
    monkeypatch.setattr(setup_checks.subprocess, "run", fake_run)
    answer.update(loggedIn=False)
    status = setup_checks.claude_status()
    assert status["found"] and not status["logged_in"]

    answer.update(loggedIn=True, email="me@example.com")
    assert setup_checks.claude_status()["account"] == "me@example.com"


def test_settings_and_backups(store):
    from arete import setup_checks
    setup_checks.save_settings(wizard_done=False, other=1)
    assert setup_checks.load_settings() == {"wizard_done": False, "timezone": "Europe/London", "other": 1}
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

    at.button(key="wiz_next").click().run()                      # -> the assistant: optional, off to start with
    assert at.toggle(key="wiz_ai_on").value is False and not at.text_input
    at.toggle(key="wiz_ai_on").set_value(True).run()             # on: how to connect Claude (an API key by default)
    assert at.text_input[0].label == "Anthropic API key"
    at.radio(key="wiz_claude_way").set_value("Use my Claude subscription (Claude Code)").run()
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


def test_api_key_setup(store, monkeypatch):
    """Pasting a key: it's checked, saved in the (fake) secret store, and then Claude counts as ready."""
    from arete import assistant
    from arete import setup_checks
    monkeypatch.setattr(assistant, "find_claude", lambda: "/sdk/_bundled/claude")
    monkeypatch.setattr(assistant, "bundled_claude", lambda: "/sdk/_bundled/claude")
    status = setup_checks.claude_status()
    assert not status["logged_in"] and status["bundled"] and not status["found"]

    checked = []
    monkeypatch.setattr(assistant, "check_api_key", lambda key: (checked.append(key) or key.endswith("good"), "nope"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Settings").run()
    at.text_input[[t.label for t in at.text_input].index("Anthropic API key")].input("sk-ant-bad")
    at.button[[b.label for b in at.button].index("Save key")].click().run()
    assert checked == ["sk-ant-bad"] and not assistant.saved_api_key()
    assert any(e.value == "nope" for e in at.error)

    at.text_input[[t.label for t in at.text_input].index("Anthropic API key")].input("sk-ant-good")
    at.button[[b.label for b in at.button].index("Save key")].click().run()
    assert assistant.api_key() == "sk-ant-good"
    assert setup_checks.claude_status()["account"] == "API key"
    assert assistant._env("/sdk/_bundled/claude")["ANTHROPIC_API_KEY"] == "sk-ant-good"
    assert any("using an Anthropic API key" in s.value for s in at.success)

    assistant.forget_api_key()
    assert assistant.api_key() is None


def test_check_api_key_messages(monkeypatch):
    from arete import assistant
    assert not assistant.check_api_key("hello")[0]                       # not even the right shape

    class Reply:
        def __init__(self, code):
            self.status_code = code
    for code, ok, words in ((200, True, "works"), (401, False, "didn't accept"), (500, False, "error (500)")):
        monkeypatch.setattr(assistant.requests, "get", lambda *a, code=code, **k: Reply(code))
        result = assistant.check_api_key("sk-ant-x")
        assert result[0] is ok and words in result[1] and "sk-ant-x" not in result[1]


def test_wizard_without_the_assistant(store):
    """Leaving the assistant off: no microphone step, and no chat anywhere in the app."""
    from arete import setup_checks
    os.remove(store.DATA_DIR / "settings.json")
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.button(key="wiz_next").click().run()                      # -> the assistant (left off)
    at.button(key="wiz_next").click().run()                      # -> Calendar
    at.button(key="wiz_next").click().run()                      # -> straight to Done (no microphone)
    assert at.title[0].value == "You're all set"
    at.button(key="wiz_back").click().run()                      # Back skips it too
    assert any("Publish a calendar" in m.value for m in at.markdown)
    at.button(key="wiz_next").click().run()
    at.button(key="wiz_finish").click().run()
    assert setup_checks.load_settings()["ai_enabled"] is False
    assert not at.exception and at.sidebar.radio[0].value == "Home"
    assert not [t for t in at.text_area if "Arete" in (t.placeholder or "")]   # no ask box
    for page in at.sidebar.radio[0].options:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page
    at.sidebar.radio[0].set_value("Settings").run()
    at.toggle(key="set_ai_on").set_value(True).run()             # switched on later in Settings
    assert setup_checks.load_settings()["ai_enabled"] is True and not at.exception


def test_appearance_setting(store):
    from arete.ui import appearance
    assert appearance.mode() == "System"
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Settings").run()
    at.segmented_control(key="set_theme").set_value("Dark").run()
    assert not at.exception and store.load_settings()["theme"] == "Dark" and appearance.mode() == "Dark"
    store.save_settings(theme="Purple")                         # anything odd falls back to System
    assert appearance.mode() == "System"


def test_automatic_backups(store, tmp_path):
    from datetime import date as d
    from arete import setup_checks
    folder = tmp_path / "cloud" / "Arete backups"
    assert setup_checks.set_backups_dir(str(folder)) == folder and setup_checks.backups_dir() == folder
    made = setup_checks.auto_backup_if_due()
    assert made and made.parent == folder
    assert setup_checks.auto_backup_if_due() is None                 # one a day
    # Keeps only the newest N plain backups; others (e.g. -before-todos) are left alone.
    for day in range(1, 8):
        (folder / f"life-2026-09-0{day}-120000.db").write_bytes(b"x")
    (folder / "life-2026-01-01-120000-before-todos.db").write_bytes(b"x")
    store.save_settings(backup_keep=3)
    for p in folder.glob("life-*.db"):                               # pretend today's isn't made yet
        if p.name.startswith(f"life-{d.today().isoformat()}"):
            p.unlink()
    setup_checks.auto_backup_if_due()
    names = sorted(p.name for p in folder.glob("life-*.db"))
    assert len([n for n in names if "before" not in n]) == 3 and "life-2026-01-01-120000-before-todos.db" in names
    store.save_settings(auto_backup=False)
    for p in folder.glob(f"life-{d.today().isoformat()}*"):
        p.unlink()
    assert setup_checks.auto_backup_if_due() is None                 # switched off
    with pytest.raises(ValueError):
        setup_checks.set_backups_dir("relative/path")
    setup_checks.set_backups_dir(None)
    assert setup_checks.backups_dir() == store.DATA_DIR / "backups"


def test_text_size_setting(store):
    from arete.ui import appearance
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not any("font-size: 20px" in h.value for h in at.get("html"))
    at.sidebar.radio[0].set_value("Settings").run()
    at.segmented_control(key="set_text_size").set_value("Extra large").run()
    assert not at.exception and appearance.text_size() == "Extra large"
    assert any("html { font-size: 20px !important; }" in h.value for h in at.get("html"))


def test_time_zone(store, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    assert store.zone_name() == "Europe/London"                  # (the tests pin it)
    store.save_settings(timezone="America/New_York")
    assert store.local_today() == datetime.now(ZoneInfo("America/New_York")).date()
    assert store.stamp().endswith(("-04:00", "-05:00"))
    store.save_settings(timezone="Not/AZone")                    # nonsense: the computer's zone
    monkeypatch.setattr(store, "computer_zone_name", lambda: "Asia/Tokyo")
    assert store.zone_name() == "Asia/Tokyo"
    store.save_settings(timezone=None)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Settings").run()
    assert not at.exception and at.toggle(key="region_tz_auto").value is True
    at.toggle(key="region_tz_auto").set_value(False).run()
    at.selectbox(key="region_tz").set_value("Australia/Sydney").run()
    assert store.load_settings()["timezone"] == "Australia/Sydney" and not at.exception
