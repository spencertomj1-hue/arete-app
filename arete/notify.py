"""
Desktop reminders: a notification a few minutes before each block in the Plan
(and each visible Outlook event) starts.

A background thread, started once per app process from app.py, checks every 30
seconds. Settings (settings.json): "reminders" (on/off, default on) and
"reminder_minutes" (how long before, default 5). Each block is announced once.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta

from arete import store, system

log = logging.getLogger(__name__)

CHECK_EVERY = 30          # seconds
DEFAULT_MINUTES = 5


def settings():
    s = store.load_settings()
    return bool(s.get("reminders", True)), int(s.get("reminder_minutes", DEFAULT_MINUTES))


def starts_at(day, hhmm):
    """When a block on a timetable day really starts (times before 07:00 are after midnight)."""
    t = datetime.combine(store._as_date(day), datetime.strptime(hhmm, "%H:%M").time())
    return t + timedelta(days=1) if t.hour * 60 + t.minute < store.FIRST_MIN else t


def upcoming(now):
    """[(starts, block)] for the Plan blocks and timed Outlook events around now."""
    first, last = (now.date() - timedelta(days=1)).isoformat(), now.date().isoformat()   # after midnight: yesterday's day
    items = [(starts_at(b["date"], b["start"]), b)
             for b in store.blocks_between(first, last, "plan")]
    items += [(starts_at(e["date"], e["start"]), {"date": e["date"], "start": e["start"], "end": e["end"],
                                                  "label": e["title"], "details": e.get("location")})
              for e in store.outlook_between(first, last) if not e["all_day"] and e["start"]]
    return sorted(items, key=lambda x: x[0])


def due(now, minutes, sent):
    """[(key, title, body)] to announce now: blocks starting within `minutes`, not yet announced."""
    out = []
    for starts, b in upcoming(now):
        key = f"{b['date']}|{b['start']}|{b['label']}"
        if key in sent or not starts - timedelta(minutes=minutes) <= now < starts + timedelta(minutes=1):
            continue
        left = round((starts - now).total_seconds() / 60)
        when = "now" if left <= 0 else f"in {left} min"
        body = f"{b['start']}–{b['end']}, {when}"
        if b.get("details"):
            body += f"\n{str(b['details']).splitlines()[0][:100]}"
        out.append((key, b["label"] or "Next block", body))
    return out


def check(sent, now=None):
    """Announce whatever is due; returns what was sent."""
    on, minutes = settings()
    if not on:
        return []
    now = now or datetime.now(store.TZ).replace(tzinfo=None)
    out = due(now, minutes, sent)
    for key, title, body in out:
        sent.add(key)
        system.notify(title, body)
    # Forget announcements from before yesterday.
    cutoff = (now.date() - timedelta(days=2)).isoformat()
    for key in [k for k in sent if k < cutoff]:
        sent.discard(key)
    return out


_lock = threading.Lock()
_thread = None


def start():
    """Start the reminder thread (once per process). ARETE_NO_REMINDERS=1 turns it off (tests)."""
    global _thread
    if os.environ.get("ARETE_NO_REMINDERS"):
        return
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="arete-reminders", daemon=True)
        _thread.start()


def _loop():
    sent = set()
    while True:
        try:
            check(sent)
        except Exception:                      # a bad check must never kill the thread
            log.exception("Reminder check failed")
        time.sleep(CHECK_EVERY)
