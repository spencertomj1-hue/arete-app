"""Outlook import: parsing, the timetable day rule, snapping and sync (no network)."""

import importlib
from datetime import date, datetime
from pathlib import Path

import pytest

FIXTURE = (Path(__file__).parent / "fixtures" / "sample.ics").read_bytes()
TODAY = date(2026, 10, 29)                      # window: 15 Oct -> 24 Dec 2026


@pytest.fixture
def outlook(store, tmp_path, monkeypatch):
    from arete import outlook as module
    module = importlib.reload(module)             # picks up the reloaded store
    monkeypatch.setattr(module, "CACHE", tmp_path / "cache.ics")
    from conftest import MemoryKeyring
    monkeypatch.setattr(module, "keyring", MemoryKeyring())   # reload brought back the real Keychain
    module.save_url("https://example.invalid/private/calendar.ics")   # a connected (fake) calendar
    return module


def by_title(rows):
    out = {}
    for r in rows:
        out.setdefault(r["title"], []).append((r["date"], r["start"], r["end"]))
    return out


def test_parse_expands_recurring_with_exceptions(outlook):
    events = outlook.parse(FIXTURE, TODAY)
    chem = sorted((e["start"], e["title"]) for e in events if e["title"].startswith("Chemistry 101"))
    starts = [(s.strftime("%Y-%m-%d %H:%M"), t) for s, t in chem]
    # 12 Oct is before the window, 19 Oct is an EXDATE, 26 Oct was moved to 14:00.
    assert starts[:3] == [("2026-10-26 14:00", "Chemistry 101 (moved)"), ("2026-11-02 09:00", "Chemistry 101"),
                          ("2026-11-09 09:00", "Chemistry 101")]
    # COUNT=10 from 5 Oct ends 7 Dec: in the window that's 26 Oct, 2-30 Nov and 7 Dec.
    assert len(starts) == 7 and starts[-1] == ("2026-12-07 09:00", "Chemistry 101")
    # After the clocks went back (25 Oct) the lecture is still 09:00 London time.
    assert all(s.tzinfo.key == "Europe/London" for s, _ in chem)


def test_parse_skips_cancelled_and_out_of_window(outlook):
    titles = {e["title"] for e in outlook.parse(FIXTURE, TODAY)}
    assert "Cancelled seminar" not in titles                 # STATUS:CANCELLED
    assert "Canceled: Team dinner" not in titles             # Outlook's "Canceled:" title, STATUS still CONFIRMED
    assert "Next summer" not in titles


def test_windows_timezone_without_vtimezone(outlook):
    call = next(e for e in outlook.parse(FIXTURE, TODAY) if e["title"] == "US call")
    # 01:00 Pacific (still on daylight time, UTC-7) is 08:00 in London (GMT).
    assert call["start"].strftime("%H:%M") == "08:00" and call["end"].strftime("%H:%M") == "09:00"


def test_rows_snap_clip_and_date_rule(outlook):
    rows = by_title(outlook.to_rows(outlook.parse(FIXTURE, TODAY)))
    assert rows["Tutorial"] == [("2026-10-29", "09:00", "10:00")]            # 09:10-09:50 snaps outwards
    assert rows["Early gym"] == [("2026-10-29", "07:00", "08:00")]      # 06:00 clipped to the grid
    assert rows["Late lab"] == [("2026-10-30", "23:30", "01:00")]       # crosses midnight, ends 00:45 -> 01:00
    assert rows["Night owl"] == [("2026-10-30", "00:00", "01:00")]      # starts 00:15 on the 31st -> the 30th
    assert rows["US call"] == [("2026-10-29", "08:00", "09:00")]


def test_all_day_rows(outlook):
    rows = [r for r in outlook.to_rows(outlook.parse(FIXTURE, TODAY)) if r["all_day"]]
    assert [(r["date"], r["title"], r["start"]) for r in rows] == [
        ("2026-11-02", "Reading week", None), ("2026-11-03", "Reading week", None)]


def test_exact_times_kept_for_popover(outlook):
    tut = next(r for r in outlook.to_rows(outlook.parse(FIXTURE, TODAY)) if r["title"] == "Tutorial")
    assert tut["exact"] == "09:10–09:50"


def test_sync_stores_rows_and_caches(outlook, store):
    status = outlook.sync(TODAY, fetcher=lambda url: FIXTURE)
    assert status["ok"] and status["count"] > 0 and outlook.CACHE.exists()
    titles = {r["title"] for r in store.outlook_between("2026-10-26", "2026-11-03")}
    assert {"Tutorial", "Chemistry 101", "Reading week", "Night owl"} <= titles


def test_sync_offline_keeps_cached_events(outlook, store):
    outlook.sync(TODAY, fetcher=lambda url: FIXTURE)

    def offline(url):
        raise outlook.SyncError("Couldn't reach Outlook (offline?).")

    status = outlook.sync(TODAY, fetcher=offline)
    assert not status["ok"] and "offline" in status["error"]
    assert status["synced_at"] is not None
    assert store.outlook_between("2026-10-29", "2026-10-29")       # still showing cached events


def test_sync_errors_never_contain_the_url(outlook, monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.ConnectionError("Max retries exceeded with url: https://example.invalid/private/calendar.ics")

    monkeypatch.setattr(requests, "get", boom)
    status = outlook.sync(TODAY)
    assert "example.invalid" not in status["error"] and "private" not in status["error"]


def test_sync_replaces_old_events(outlook, store):
    outlook.sync(TODAY, fetcher=lambda url: FIXTURE)
    empty = b"BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"
    outlook.sync(TODAY, fetcher=lambda url: empty)
    assert store.outlook_between("2026-01-01", "2027-12-31") == []


def test_bad_calendar_keeps_previous_cache(outlook, store):
    outlook.sync(TODAY, fetcher=lambda url: FIXTURE)
    status = outlook.sync(TODAY, fetcher=lambda url: b"BEGIN:VCALENDAR\nthis is not ical")
    assert not status["ok"]
    assert outlook.CACHE.read_bytes() == FIXTURE                   # good cache not overwritten
    assert store.outlook_between("2026-10-29", "2026-10-29")


def test_category_and_overlaps(outlook, store):
    outlook.sync(TODAY, fetcher=lambda url: FIXTURE)
    assert store.outlook_category("Chemistry 101") == "classes"   # Outlook events use one category
    assert store.outlook_category("Night owl") == "classes"
    assert "Outlook" not in set(store.load_categories()["name"])   # no separate category
    assert [r["title"] for r in store.outlook_overlaps("2026-10-29", 4, 5)] == ["Tutorial"]   # 09:00-09:30
    assert store.outlook_overlaps("2026-10-29", 6, 8) == []


def test_ago(outlook):
    now = datetime(2026, 10, 29, 12, 0)
    assert outlook.ago(datetime(2026, 10, 29, 11, 55), now) == "5 min ago"
    assert outlook.ago(datetime(2026, 10, 29, 9, 0), now) == "3 h ago"
    assert outlook.ago(None) == "never"


def test_outlook_events_can_be_removed_and_adjusted(outlook, store):
    from arete import tools
    tools = importlib.reload(tools)
    outlook.sync(TODAY, fetcher=lambda url: FIXTURE)
    listed = tools.query_blocks("2026-10-29", "2026-10-29").text
    acp_id = next(l for l in listed.splitlines() if "Tutorial" in l).split()[2].rstrip(":")
    assert acp_id.startswith("outlook:") and "exactly 09:10–09:50" in listed

    # Adding a block over an Outlook event: previewed, then the event is trimmed like any block.
    r = tools.add_block("2026-10-29", "plan", "09:30", "10:00", "Coffee", category="Meals")
    assert r.pending and "Tutorial" in r.pending.summary
    r.pending.run()
    plan = [(b["start"], b["end"], b["label"]) for b in store.blocks_between("2026-10-29", "2026-10-29", "plan")]
    assert plan == [("09:00", "09:30", "Tutorial"), ("09:30", "10:00", "Coffee")]
    assert "Tutorial" not in {e["title"] for e in store.outlook_between("2026-10-29", "2026-10-29")}   # now the user's own

    # Removing an Outlook event hides it from the timetable, and it stays hidden after a re-sync.
    gym_id = next(l for l in tools.query_blocks("2026-10-29", "2026-10-29").text.splitlines()
                  if "Early gym" in l).split()[2].rstrip(":")
    r = tools.delete_block(gym_id)
    assert r.pending and "stays in Outlook" in r.pending.summary
    r.pending.run()
    outlook.sync(TODAY, fetcher=lambda url: FIXTURE)
    assert "Early gym" not in {e["title"] for e in store.outlook_between("2026-10-29", "2026-10-29")}

    # Changing one turns it into the user's own block, keeping its location in the description.
    call_id = next(l for l in tools.query_blocks("2026-10-29", "2026-10-29").text.splitlines()
                   if "US call" in l).split()[2].rstrip(":")
    tools.update_block(call_id, label="Call with Sam").pending.run()
    own = {b["label"]: b for b in store.blocks_between("2026-10-29", "2026-10-29", "plan")}
    assert own["Call with Sam"]["start"] == "08:00" and own["Call with Sam"]["id"].startswith("ol:")


def test_grid_ops_hide_and_detach(outlook, store):
    outlook.sync(TODAY, fetcher=lambda url: FIXTURE)
    tut = next(e for e in store.outlook_between("2026-10-29", "2026-10-29") if e["title"] == "Tutorial")
    assert store.detach_outlook(tut["uid"]) == f"ol:{tut['uid']}"
    assert store.get_block(f"ol:{tut['uid']}")["label"] == "Tutorial"
    assert store.detach_outlook(tut["uid"]) == f"ol:{tut['uid']}"          # idempotent
    late = next(e for e in store.outlook_between("2026-10-30", "2026-10-30") if e["title"] == "Late lab")
    store.hide_outlook(late["uid"])
    assert "Late lab" not in {e["title"] for e in store.outlook_between("2026-10-30", "2026-10-30")}


def test_short_description_drops_meeting_boilerplate(outlook):
    text = ("Bring your lab book.\nWe'll cover chapter 3.\n\n________________________________\n"
            "Microsoft Teams meeting\nJoin on your computer, mobile app or room device\n"
            "Click here to join the meeting\nMeeting ID: 123 456 789\nPasscode: abc")
    assert outlook.short_description(text) == "Bring your lab book. We'll cover chapter 3."
    assert outlook.short_description("x" * 500).endswith("…") and len(outlook.short_description("x" * 500)) == 300
    assert outlook.short_description("   ") is None


def test_outlook_category_setting(outlook, store):
    from arete import tools
    tools = importlib.reload(tools)
    assert tools.set_outlook_category("Study").changed
    assert store.outlook_category("anything") == "study"
