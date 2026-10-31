"""
One-way import of the Outlook calendar into the timetable (read only: nothing
is ever written back to Outlook).

  fetch()   download the published .ics (its link is kept only in the macOS Keychain)
  parse()   expand recurring events (with their exceptions) in a window of 14 days back to
            8 weeks ahead, convert to the user's time zone, skip cancelled events
  to_rows() fit events to the timetable grid: 07:00-01:00, after-midnight slots belong to the
            day before, start rounds down / end rounds up to 30 minutes
  sync()    all of the above, storing the rows in tt_outlook. The last good .ics is cached,
            so when offline the timetable keeps showing the cached events.

The URL is private: never log or display it. Error messages here never include it
(requests puts the URL in its exception text, so those are replaced).
"""

import math
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import icalendar
import keyring
import recurring_ical_events
import requests
from icalendar.timezone.windows_to_olson import WINDOWS_TO_OLSON

from arete import store

CACHE = store.DATA_DIR / ".outlook_cache.ics"   # last good download (gitignored)
KEYCHAIN_SERVICE = "Arete"                 # the link is stored in the macOS Keychain
KEYCHAIN_ACCOUNT = "outlook-ics-url"
OUTLOOK_HOSTS = ("outlook.office365.com", "outlook.office.com", "outlook.live.com", "outlook.com")

DAYS_BACK = 14
WEEKS_AHEAD = 8
TIMEOUT = 10                               # seconds
SLOT = timedelta(minutes=30)
GRID_START = time(7, 0)                    # a timetable day runs 07:00 -> 01:00 the next day
GRID_END = time(1, 0)


class SyncError(Exception):
    """Couldn't get the calendar. The message is safe to show (it never contains the URL).
    kind: "timeout", "offline", "http" (see .status), "notcal" or "error"."""

    def __init__(self, message, kind="error", status=None):
        super().__init__(message)
        self.kind, self.status = kind, status


class LinkError(Exception):
    """A pasted calendar link that isn't usable (the message says why)."""


# ---------------------------------------------------------------------------
# Where the link is kept: only in the macOS Keychain
# ---------------------------------------------------------------------------

def ics_url():
    """The calendar URL, or None if no calendar is connected."""
    try:
        return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT) or None
    except Exception:
        return None


def save_url(url):
    keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, url)


def disconnect():
    """Forget the link (Keychain) and the imported events."""
    try:
        keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except Exception:
        pass
    CACHE.unlink(missing_ok=True)
    store.replace_outlook([])


# ---------------------------------------------------------------------------
# Connecting: check a pasted link, then try it
# ---------------------------------------------------------------------------

def check_link(text):
    """
    Validate a pasted link: https, an Outlook domain, ending in .ics. A pasted
    .html link (the web view) is swapped for the .ics one. Returns (url, note).
    """
    url = (text or "").strip().strip("\"'<> ")
    if not url:
        raise LinkError("Paste the ICS link from Outlook first.")
    parts = urlparse(url)
    if parts.scheme != "https":
        raise LinkError("That doesn't look like a web link. It should start with https://outlook.office365.com/…")
    host = (parts.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in OUTLOOK_HOSTS):
        raise LinkError("That isn't an Outlook calendar link. It should start with https://outlook.office365.com/… "
                        "(or outlook.office.com / outlook.live.com).")
    note = None
    if parts.path.lower().endswith(".html"):
        parts = parts._replace(path=parts.path[:-5] + ".ics")
        url = urlunparse(parts)
        note = "You pasted the HTML link (for viewing in a browser), so Arete switched it to the ICS link."
    if not parts.path.lower().endswith(".ics"):
        raise LinkError("The link should end in .ics. In Outlook, copy the ICS link, not the HTML one.")
    return url, note


def friendly(error):
    """A plain-English explanation of a failed test fetch."""
    if error.kind in ("offline", "timeout"):
        return "Couldn't reach Outlook. Check you're connected to the internet, then try again."
    if error.kind == "http" and error.status in (401, 403):
        return ("Outlook refused this link. Some university and work accounts don't allow publishing "
                "calendars; if Outlook didn't offer \"Publish a calendar\", ask your IT team. If you reset or "
                "unpublished the link, publish again and paste the new ICS link.")
    if error.kind == "http" and error.status == 404:
        return ("Outlook couldn't find that calendar. The link may have been reset or unpublished. "
                "Publish it again in Outlook and paste the new ICS link.")
    if error.kind == "notcal":
        return "That link didn't return a calendar. Make sure you copied the ICS link (ending in .ics)."
    return str(error)


def try_link(url, fetcher=None, today=None):
    """
    Test-download a calendar link. Returns {data, count, preview}: the number of
    events in the next 2 weeks and the next 3 of them. Raises SyncError.
    """
    data = (fetcher or fetch)(url)
    now = datetime.now(store.tz())
    soon = []
    for ev in parse(data, today):
        start = ev["start"] if not ev["all_day"] else datetime.combine(ev["start"], time(0), store.tz())
        if now - timedelta(hours=1) <= start <= now + timedelta(days=14):
            soon.append((start, ev))
    soon.sort(key=lambda p: p[0])
    preview = [{"title": ev["title"],
                "when": f"{s:%a} {s.day} {s:%b}, " + ("all day" if ev["all_day"] else f"{s:%H:%M}")}
               for s, ev in soon[:3]]
    return {"data": data, "count": len(soon), "preview": preview}


def connect(url, data):
    """Save a working link to the Keychain and import its events."""
    save_url(url)
    return sync(fetcher=lambda _: data)


def fetch(url):
    """Download the .ics. Raises SyncError (without the URL in it) on any failure."""
    try:
        response = requests.get(url, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.Timeout:
        raise SyncError("Outlook didn't respond in time.", "timeout") from None
    except requests.ConnectionError:
        raise SyncError("Couldn't reach Outlook (offline?).", "offline") from None
    except requests.HTTPError:
        status = response.status_code
        raise SyncError(f"Outlook refused the request (HTTP {status}).", "http", status) from None
    except requests.RequestException as e:
        raise SyncError(f"Couldn't download the calendar ({type(e).__name__}).") from None
    if not response.content.lstrip().startswith(b"BEGIN:VCALENDAR"):
        raise SyncError("The calendar link didn't return a calendar.", "notcal")
    return response.content


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _to_london(value, tzid=None):
    """Aware datetime in the user's time zone (store.tz()). Naive times use their TZID (Windows names mapped)."""
    if value.tzinfo is None:
        try:
            zone = ZoneInfo(WINDOWS_TO_OLSON.get(tzid, tzid)) if tzid else store.tz()
        except (ZoneInfoNotFoundError, ValueError):
            zone = store.tz()
        value = value.replace(tzinfo=zone)
    return value.astimezone(store.tz())


def parse(ics_bytes, today=None):
    """Events in the sync window, as dicts: uid, title, location, all_day, start, end."""
    today = today or store.local_today()
    window = (today - timedelta(days=DAYS_BACK), today + timedelta(weeks=WEEKS_AHEAD))
    calendar = icalendar.Calendar.from_ical(ics_bytes)

    events = []
    for ev in recurring_ical_events.of(calendar).between(*window):
        # Skip cancelled events. Outlook usually doesn't set STATUS:CANCELLED on meetings
        # someone else cancelled: it keeps them CONFIRMED with a "Canceled: ..." title.
        title = str(ev.get("SUMMARY", "")).strip()
        if str(ev.get("STATUS", "")).upper() == "CANCELLED" or title.lower().startswith(("canceled:", "cancelled:")):
            continue
        start = ev.decoded("DTSTART")
        if "DTEND" in ev:
            end = ev.decoded("DTEND")
        elif "DURATION" in ev:
            end = start + ev.decoded("DURATION")
        else:
            end = start + (timedelta(days=1) if not isinstance(start, datetime) else timedelta(0))
        info = {
            "uid": str(ev.get("UID", "")),
            "title": title or "(No title)",
            "location": str(ev.get("LOCATION", "")).strip().strip("-–— ") or None,   # "-" means none
            "description": short_description(str(ev.get("DESCRIPTION", ""))),
        }
        if isinstance(start, datetime):
            tzid = ev["DTSTART"].params.get("TZID")
            events.append({**info, "all_day": False, "start": _to_london(start, tzid), "end": _to_london(end, tzid)})
        else:
            events.append({**info, "all_day": True, "start": start, "end": end})   # dates; end exclusive
    return events


def short_description(text, limit=300):
    """The event's notes, without the Teams/Zoom join boilerplate, cut to a short snippet."""
    lines = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        low = line.lower()
        if not line or set(line) <= set("_-=*~ "):
            continue
        if low.startswith(("microsoft teams", "join the meeting", "join on your computer", "meeting id",
                           "passcode", "need help?", "for organizers", "join:", "https://teams.", "<https://")):
            break                                    # the rest is the dial-in block
        lines.append(line)
    snippet = " ".join(lines)
    return (snippet[:limit - 1].rstrip() + "…" if len(snippet) > limit else snippet) or None


def _grid_rows(ev):
    """Pieces of a timed event that fall on timetable days, snapped to 30-minute slots."""
    s, e = ev["start"], ev["end"]
    exact = f"{s:%H:%M}–{e:%H:%M}" if s.date() == e.date() else f"{s:%a %H:%M} – {e:%a %H:%M}"
    rows = []
    day = s.date() - timedelta(days=1)          # an event at 00:30 belongs to the day before
    while day <= e.date():
        g0 = datetime.combine(day, GRID_START, store.tz())
        g1 = datetime.combine(day + timedelta(days=1), GRID_END, store.tz())
        a, b = max(s, g0), min(e, g1)            # clip to the grid
        if a < b:
            first = math.floor((a - g0) / SLOT)   # start rounds down
            last = math.ceil((b - g0) / SLOT)     # end rounds up
            rows.append({
                "uid": f"{ev['uid']}|{s.isoformat()}|{day}", "date": day.isoformat(),
                "start": store.slot_time(first), "end": store.slot_time(min(last, store.SLOTS)),
                "title": ev["title"], "location": ev["location"], "all_day": 0, "exact": exact,
                "description": ev.get("description"),
            })
        day += timedelta(days=1)
    return rows


def to_rows(events):
    """tt_outlook rows for parsed events (all-day events get one row per day)."""
    rows = []
    for ev in events:
        if not ev["all_day"]:
            rows += _grid_rows(ev)
            continue
        day = ev["start"]
        while day < ev["end"]:
            rows.append({"uid": f"{ev['uid']}|{day}", "date": day.isoformat(), "start": None, "end": None,
                         "title": ev["title"], "location": ev["location"], "all_day": 1, "exact": None,
                         "description": ev.get("description")})
            day += timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def last_synced():
    """When the cached calendar was last downloaded, or None."""
    return datetime.fromtimestamp(CACHE.stat().st_mtime) if CACHE.exists() else None


def sync(today=None, fetcher=fetch):
    """
    Download, parse and store the calendar. If the download fails, the cached copy
    is used so the timetable keeps showing events. Returns a status dict:
    {ok, error, synced_at, count}.
    """
    url = ics_url()
    if not url:
        return {"ok": False, "error": "No Outlook calendar is set up.", "synced_at": None, "count": 0}

    error = None
    try:
        data = fetcher(url)
        rows = to_rows(parse(data, today))     # check it parses before replacing the cache
        CACHE.write_bytes(data)
    except SyncError as e:
        error, rows = str(e), None
    except Exception as e:                     # a malformed calendar
        error, rows = f"Couldn't read the calendar ({type(e).__name__}).", None

    if rows is None and CACHE.exists():
        try:
            rows = to_rows(parse(CACHE.read_bytes(), today))   # re-parse so the window moves on
        except Exception:
            rows = None
    if rows is not None:
        store.replace_outlook(rows)
    return {"ok": error is None, "error": error, "synced_at": last_synced(), "count": len(rows or [])}


def ago(when, now=None):
    """'just now', '5 min ago', '3 h ago', '2 days ago'."""
    if when is None:
        return "never"
    minutes = int(((now or datetime.now()) - when).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"
