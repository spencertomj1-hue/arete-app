"""
Data layer for Arete: every SQL query lives here. No Streamlit code, so the
pages, the Claude tools and the tests all share the same logic.

All user data lives in the data folder: ~/Library/Application Support/Arete
(or $ARETE_DATA_DIR, used for tests and the demo). The database is life.db
there (or $ARETE_DB). Backups, settings.json and the Outlook cache live there
too. Nothing user-specific is kept in the project folder.
"""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

DATA_DIR = Path(os.environ.get("ARETE_DATA_DIR") or Path.home() / "Library" / "Application Support" / "Arete")
DB_PATH = Path(os.environ.get("ARETE_DB") or DATA_DIR / "life.db")

# Habits appear as extra columns in the History table and CSV, named "habit: <name>".
HABIT_PREFIX = "habit: "

# Timetable: 36 half-hour slots from 07:00 to 01:00 the next day.
SLOTS = 36
FIRST_MIN = 7 * 60
TRACKS = ("plan", "actual")

# The categories a new install starts with: generic, and editable by asking Claude.
# (id, name, colour, text colour, group) in key order.
DEFAULT_CATEGORIES = [
    ("sleep", "Sleep", "#D0D0D0", None, "Life"),
    ("meals", "Meals", "#F7C7AC", None, "Life"),
    ("exercise", "Exercise", "#FFC000", None, "Life"),
    ("social", "Social", "#D86DCD", None, "Life"),
    ("rest", "Rest", "#E49EDD", None, "Life"),
    ("reading", "Reading", "#7030A0", "#FFFFFF", "Life"),
    ("commute", "Commute", "#FFF7B8", None, "Life"),
    ("chores", "Chores", "#92D050", None, "Life"),
    ("distracted", "Distracted", "#FF0000", None, "Life"),
    ("classes", "Classes", "#00B0F0", None, "Work"),
    ("study", "Study", "#00B050", None, "Work"),
    ("work", "Work", "#83CCEB", None, "Work"),
    ("projects", "Projects", "#DAF2D0", None, "Work"),
]

# Outlook events use this category unless settings.json says otherwise ("outlook_category").
DEFAULT_OUTLOOK_CATEGORY = "classes"

# Typing one of these activities picks its category automatically.
DEFAULT_LABEL_MAP = {
    "Sleep": "sleep", "Breakfast": "meals", "Lunch": "meals", "Dinner": "meals",
    "Gym": "exercise", "Run": "exercise", "Lecture": "classes", "Seminar": "classes",
    "Homework": "study", "Revision": "study", "Commute": "commute",
}


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------

@contextmanager
def db():
    """Open a connection, commit on success, and always close it."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")  # needed for ON DELETE CASCADE
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist yet, and seed the timetable key."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            -- One row per day
            CREATE TABLE IF NOT EXISTS entries (
                date   TEXT PRIMARY KEY,   -- ISO format, e.g. 2026-10-29
                mood   INTEGER,            -- 1-5
                sleep  REAL,               -- hours
                energy INTEGER,            -- 1-5
                notes  TEXT
            );

            -- The list of habits you track
            CREATE TABLE IF NOT EXISTS habits (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );

            -- A row here means "habit was done on this date"
            CREATE TABLE IF NOT EXISTS habit_log (
                habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
                date     TEXT NOT NULL,
                PRIMARY KEY (habit_id, date)
            );

            -- Timetable
            CREATE TABLE IF NOT EXISTS tt_categories (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                colour      TEXT NOT NULL,     -- "#RRGGBB"
                text_colour TEXT,              -- NULL = pick black/white automatically
                grp         TEXT NOT NULL,     -- group heading in the key, e.g. "Life"
                ord         INTEGER NOT NULL   -- position in the key
            );

            CREATE TABLE IF NOT EXISTS tt_entries (
                id          TEXT PRIMARY KEY,
                date        TEXT NOT NULL,     -- day the row belongs to (after-midnight slots too)
                track       TEXT NOT NULL CHECK (track IN ('plan', 'actual')),
                start       TEXT NOT NULL,     -- "HH:mm", 30-min aligned
                "end"       TEXT NOT NULL,     -- "HH:mm", exclusive; may be "01:00"
                label       TEXT NOT NULL,
                category_id TEXT,
                details     TEXT               -- optional description (e.g. kept from an Outlook event)
            );
            CREATE INDEX IF NOT EXISTS tt_entries_day ON tt_entries (date, track);

            CREATE TABLE IF NOT EXISTS tt_label_map (
                label       TEXT PRIMARY KEY,
                category_id TEXT NOT NULL
            );

            -- Events imported (read only) from the Outlook calendar. Fully replaced on
            -- each sync and kept apart from tt_entries, so the user's own blocks are never touched.
            CREATE TABLE IF NOT EXISTS tt_outlook (
                uid      TEXT PRIMARY KEY,   -- event UID + occurrence + grid day
                date     TEXT NOT NULL,      -- timetable day (after-midnight slots belong to the day before)
                start    TEXT,               -- "HH:mm" snapped to the grid; NULL for all-day events
                "end"    TEXT,
                title    TEXT NOT NULL,
                location TEXT,
                all_day  INTEGER NOT NULL DEFAULT 0,
                exact    TEXT,               -- the real times, e.g. "09:10–09:50"
                description TEXT             -- short text from the event's notes
            );
            CREATE INDEX IF NOT EXISTS tt_outlook_day ON tt_outlook (date);

            -- Outlook occurrences the user removed or turned into their own block.
            -- Kept across syncs (uids are stable per occurrence), so they stay gone.
            CREATE TABLE IF NOT EXISTS tt_outlook_hidden (uid TEXT PRIMARY KEY);

            -- Goals: to-dos for a day, week, month or year
            CREATE TABLE IF NOT EXISTS goals (
                id           TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                notes        TEXT,
                period       TEXT NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly', 'yearly')),
                period_start TEXT NOT NULL,              -- the day / Monday / 1st of the month / 1 Jan
                done         INTEGER NOT NULL DEFAULT 0,
                done_at      TEXT,                       -- ISO timestamp, NULL while unticked
                "repeat"     INTEGER NOT NULL DEFAULT 0, -- 1 = a fresh copy every new period
                ord          INTEGER NOT NULL DEFAULT 0, -- position in its list
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS goals_period ON goals (period, period_start);

            -- The last period each kind of repeating goal was copied into (see roll_goals).
            CREATE TABLE IF NOT EXISTS goal_rollover (period TEXT PRIMARY KEY, period_start TEXT NOT NULL);
            """
        )
        # Columns added after the first release.
        _add_column(conn, "tt_entries", "details", "TEXT")
        _add_column(conn, "tt_outlook", "description", "TEXT")
        if conn.execute("SELECT COUNT(*) FROM tt_categories").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO tt_categories VALUES (?, ?, ?, ?, ?, ?)",
                [(cid, name, col, txt, grp, i) for i, (cid, name, col, txt, grp) in enumerate(DEFAULT_CATEGORIES)],
            )
        if conn.execute("SELECT COUNT(*) FROM tt_label_map").fetchone()[0] == 0:
            conn.executemany("INSERT INTO tt_label_map VALUES (?, ?)", DEFAULT_LABEL_MAP.items())



# ---------------------------------------------------------------------------
# Settings (settings.json in the data folder)
# ---------------------------------------------------------------------------

def settings_path():
    return DATA_DIR / "settings.json"


def load_settings():
    try:
        return json.loads(settings_path().read_text())
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    settings = {**load_settings(), **changes}
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings_path().write_text(json.dumps(settings, indent=2))
    return settings


def _add_column(conn, table, column, kind):
    """Add a column to an existing table if an older database doesn't have it yet."""
    if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _read(sql, params=()):
    """Run a query into a DataFrame, with SQL NULLs as None (NaN isn't valid JSON)."""
    with db() as conn:
        df = pd.read_sql(sql, conn, params=params)
    return df.astype(object).where(df.notna(), None)


# ---------------------------------------------------------------------------
# Daily entries
# ---------------------------------------------------------------------------

def get_entry(day):
    """Return the entry for an ISO date as a dict, or an empty dict."""
    with db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM entries WHERE date = ?", (day,)).fetchone()
    return dict(row) if row else {}


def save_entry(conn, day, mood, sleep, energy, notes):
    """Insert or update the entry for a date (uses an open connection)."""
    conn.execute(
        """
        INSERT INTO entries (date, mood, sleep, energy, notes)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            mood = excluded.mood, sleep = excluded.sleep,
            energy = excluded.energy, notes = excluded.notes
        """,
        (day, mood, sleep, energy, notes),
    )


def entries_between(first, last):
    """Daily entries between two ISO dates (inclusive), oldest first."""
    return _read("SELECT * FROM entries WHERE date BETWEEN ? AND ? ORDER BY date", (first, last))


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

def get_habits():
    """Return a list of (id, name) tuples, sorted by name."""
    with db() as conn:
        return conn.execute("SELECT id, name FROM habits ORDER BY name").fetchall()


def find_habit(name):
    """Case-insensitive lookup; returns (id, name) or None."""
    with db() as conn:
        return conn.execute(
            "SELECT id, name FROM habits WHERE lower(name) = lower(?)", (name.strip(),)
        ).fetchone()


def add_habit(name):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO habits (name) VALUES (?)", (name,))


def delete_habit(habit_id):
    # Its habit_log rows are removed too, thanks to ON DELETE CASCADE.
    with db() as conn:
        conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))


def get_habit_dates(habit_id):
    """Return the set of ISO dates on which a habit was done."""
    with db() as conn:
        rows = conn.execute("SELECT date FROM habit_log WHERE habit_id = ?", (habit_id,))
        return {r[0] for r in rows}


def set_habit_done(conn, habit_id, day, done):
    """Tick or untick a habit for a date (uses an open connection)."""
    if done:
        conn.execute("INSERT OR IGNORE INTO habit_log VALUES (?, ?)", (habit_id, day))
    else:
        conn.execute("DELETE FROM habit_log WHERE habit_id = ? AND date = ?", (habit_id, day))


def habit_ticks_between(first, last):
    """{date: [habit names ticked]} between two ISO dates (inclusive)."""
    with db() as conn:
        rows = conn.execute(
            """SELECT l.date, h.name FROM habit_log l JOIN habits h ON h.id = l.habit_id
               WHERE l.date BETWEEN ? AND ? ORDER BY l.date, h.name""",
            (first, last),
        ).fetchall()
    ticks = {}
    for day, name in rows:
        ticks.setdefault(day, []).append(name)
    return ticks


def streaks(done_dates, today=None):
    """Return (current_streak, best_streak) from a set of ISO date strings."""
    # Best streak: longest run of consecutive days.
    best = run = 0
    prev = None
    for d in sorted(date.fromisoformat(x) for x in done_dates):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d

    # Current streak: count back from today. If today isn't ticked yet,
    # start from yesterday so the streak doesn't "break" before the day is over.
    current = 0
    day = today or date.today()
    if day.isoformat() not in done_dates:
        day -= timedelta(days=1)
    while day.isoformat() in done_dates:
        current += 1
        day -= timedelta(days=1)
    return current, best


# ---------------------------------------------------------------------------
# Whole-dataset helpers (used by History and Export/Import)
#
# Everything is represented as one "wide" table: one row per date with
# mood/sleep/energy/notes plus a True/False column per habit.
# ---------------------------------------------------------------------------

def load_all_data():
    """Return all entries and habit ticks as one DataFrame, newest first."""
    with db() as conn:
        entries = pd.read_sql("SELECT * FROM entries", conn)
        logs = pd.read_sql(
            "SELECT h.name, l.date FROM habit_log l JOIN habits h ON h.id = l.habit_id", conn
        )
    habit_names = [name for _, name in get_habits()]

    # Every date that has an entry or at least one habit tick.
    all_dates = sorted(set(entries["date"]) | set(logs["date"]), reverse=True)
    df = entries.set_index("date").reindex(all_dates)
    for name in habit_names:
        done = set(logs.loc[logs["name"] == name, "date"])
        df[HABIT_PREFIX + name] = df.index.isin(done)

    df.index.name = "date"
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def _clean(value, cast):
    """Turn blanks/NaN into None, otherwise cast to a plain Python type."""
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    return cast(value)


def _is_ticked(value):
    """Interpret a CSV/table cell as a habit tick."""
    if value is None or pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "1.0", "true", "yes", "y", "x"}


def save_all_data(df, replace=False):
    """
    Write a wide DataFrame back to the database.
    replace=True wipes existing data first; otherwise rows are merged by date.
    """
    habit_cols = [c for c in df.columns if c.startswith(HABIT_PREFIX)]
    with db() as conn:
        if replace:
            conn.execute("DELETE FROM entries")
            conn.execute("DELETE FROM habits")  # also clears habit_log (cascade)

        # Make sure every habit column has a habit, then look up the ids.
        for col in habit_cols:
            conn.execute("INSERT OR IGNORE INTO habits (name) VALUES (?)", (col[len(HABIT_PREFIX):],))
        habit_ids = dict(conn.execute("SELECT name, id FROM habits").fetchall())

        for row in df.to_dict("records"):
            if row.get("date") is None or pd.isna(row.get("date")):
                continue  # skip blank rows (e.g. an empty row added in the editor)
            day = pd.to_datetime(row["date"]).date().isoformat()

            mood = _clean(row.get("mood"), int)
            sleep = _clean(row.get("sleep"), float)
            energy = _clean(row.get("energy"), int)
            notes = _clean(row.get("notes"), str)
            if any(v is not None for v in (mood, sleep, energy, notes)):
                save_entry(conn, day, mood, sleep, energy, notes)

            for col in habit_cols:
                set_habit_done(conn, habit_ids[col[len(HABIT_PREFIX):]], day, _is_ticked(row[col]))


# ---------------------------------------------------------------------------
# Timetable: categories and label map
# ---------------------------------------------------------------------------

def load_categories():
    return _read("SELECT * FROM tt_categories ORDER BY grp, ord")


def groups():
    """Category groups in the order they first appear in the key, e.g. ["Life", "Work"]."""
    with db() as conn:
        rows = conn.execute("SELECT grp FROM tt_categories ORDER BY rowid").fetchall()
    return list(dict.fromkeys(r[0] for r in rows))


def load_label_map():
    return _read("SELECT * FROM tt_label_map ORDER BY label")


def save_category(cid, name, colour, grp, text_colour=None):
    """Create or update one category (keeps its place in the key)."""
    with db() as conn:
        exists = conn.execute("SELECT ord FROM tt_categories WHERE id = ?", (cid,)).fetchone()
        if exists:
            conn.execute("UPDATE tt_categories SET name = ?, colour = ?, grp = ?, text_colour = ? WHERE id = ?",
                         (name, colour.upper(), grp, text_colour, cid))
        else:
            order = conn.execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM tt_categories WHERE grp = ?", (grp,)).fetchone()[0]
            conn.execute("INSERT INTO tt_categories VALUES (?, ?, ?, ?, ?, ?)",
                         (cid, name, colour.upper(), text_colour, grp, order))


def set_label_default(label, category_id):
    """Make an activity label default to a category."""
    with db() as conn:
        conn.execute("DELETE FROM tt_label_map WHERE lower(label) = lower(?)", (label,))
        conn.execute("INSERT INTO tt_label_map VALUES (?, ?)", (label.strip(), category_id))


def resolve_category(label=None, category=None):
    """
    Pick a category id. `category` may be an id or a name (any case); otherwise
    the label is looked up in the label map, then against category names.
    Returns None when nothing matches.
    """
    cats = load_categories()
    by_name = {n.lower(): i for i, n in zip(cats["id"], cats["name"])}
    if category:
        key = category.strip()
        if key in set(cats["id"]):
            return key
        return by_name.get(key.lower())
    if label:
        lm = load_label_map()
        for lbl, cid in zip(lm["label"], lm["category_id"]):
            if lbl.lower() == label.strip().lower():
                return cid
        return by_name.get(label.strip().lower())
    return None


# ---------------------------------------------------------------------------
# Timetable: slots and painting rules
#
# These rules are the source of truth. The grid's JS (timetable_component.js,
# paintRange) mirrors them only to show edits instantly; what gets saved is
# always computed here.
# ---------------------------------------------------------------------------

def slot_time(i):
    """Slot index (0..36) -> "HH:mm". Slot 36 is "01:00" (end of the last slot)."""
    m = (FIRST_MIN + 30 * i) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def slot_of(t):
    """"HH:mm" -> slot index. Times before 07:00 are after midnight."""
    h, m = map(int, t.split(":"))
    mins = h * 60 + m
    if mins < FIRST_MIN:
        mins += 1440
    return round((mins - FIRST_MIN) / 30)


def plan_paint(existing, day, track, s, e, block, new_id=None):
    """
    Work out how to fill slots s..e-1 of one day/track.

    existing: that day/track's blocks (dicts with id, date, track, start, end,
              label, category_id).
    block:    {"label", "category_id"} to paint, or None to clear the slots.

    Overlapped blocks are trimmed or split; the new block merges with touching
    blocks that have the same label and category.

    Returns (ops, affected): ops are ("upsert", entry) / ("delete", id) tuples,
    affected are existing blocks with a different label/category that get
    trimmed, split or removed (i.e. data that would be overwritten).
    """
    ops, affected = [], []
    for en in existing:
        es, ee = slot_of(en["start"]), slot_of(en["end"])
        if ee <= s or es >= e:
            continue
        same = block and en["label"] == block["label"] and en["category_id"] == block["category_id"]
        if not same:
            affected.append(en)
        left, right = es < s, ee > e
        if left:
            ops.append(("upsert", {**en, "end": slot_time(s)}))
        if right:
            ops.append(("upsert", {**en, "id": uuid.uuid4().hex if left else en["id"], "start": slot_time(e)}))
        if not left and not right:
            ops.append(("delete", en["id"]))

    if block:
        # Look at the state after trimming to find blocks to merge with.
        after = {en["id"]: en for en in existing}
        for kind, val in ops:
            if kind == "upsert":
                after[val["id"]] = val
            else:
                after.pop(val, None)
        ns, ne = s, e
        for en in list(after.values()):
            if en["label"] != block["label"] or en["category_id"] != block["category_id"]:
                continue
            if slot_of(en["end"]) == ns:
                ns = slot_of(en["start"])
                ops.append(("delete", en["id"]))
            elif slot_of(en["start"]) == ne:
                ne = slot_of(en["end"])
                ops.append(("delete", en["id"]))
        ops.append(("upsert", {
            "id": new_id or uuid.uuid4().hex, "date": day, "track": track,
            "start": slot_time(ns), "end": slot_time(ne),
            "label": block["label"], "category_id": block["category_id"], "details": block.get("details"),
        }))
    return ops, affected


BLOCK_COLS = 'id, date, track, start, "end", label, category_id, details'


def blocks_for(conn, day, track):
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT {BLOCK_COLS} FROM tt_entries WHERE date = ? AND track = ?",
        (day, track),
    ).fetchall()
    conn.row_factory = None
    return [dict(r) for r in rows]


def apply_block_ops(conn, ops):
    for kind, val in ops:
        if kind == "delete":
            conn.execute("DELETE FROM tt_entries WHERE id = ?", (val,))
        else:
            conn.execute(
                f"INSERT OR REPLACE INTO tt_entries ({BLOCK_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (val["id"], val["date"], val["track"], val["start"], val["end"], val["label"], val["category_id"],
                 val.get("details")),
            )


def preview_paint(day, track, s, e, block, exclude=None):
    """What painting would overwrite (Outlook events included), without changing anything."""
    with db() as conn:
        existing = [b for b in blocks_for(conn, day, track) if b["id"] != exclude]
        if track == "plan":
            existing += [_as_block(r) for r in _visible_outlook(conn, day, s, e)]
        return plan_paint(existing, day, track, s, e, block)[1]


def paint(day, track, s, e, block, new_id=None, conn=None):
    """
    Fill (or clear, when block is None) slots s..e-1. Outlook events in the way are
    first turned into the user's own blocks, so they're trimmed like any other.
    Returns the blocks it overwrote.
    """
    if conn is None:
        with db() as conn:
            return paint(day, track, s, e, block, new_id, conn)
    if track == "plan":
        for row in _visible_outlook(conn, day, s, e):
            _detach(conn, row)
    ops, affected = plan_paint(blocks_for(conn, day, track), day, track, s, e, block, new_id)
    apply_block_ops(conn, ops)
    return affected


def get_block(block_id):
    with db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {BLOCK_COLS} FROM tt_entries WHERE id = ?", (block_id,)
        ).fetchone()
    return dict(row) if row else None


def set_block_label(block_id, label, category_id, details=None):
    with db() as conn:
        conn.execute("UPDATE tt_entries SET label = ?, category_id = ?, details = ? WHERE id = ?",
                     (label, category_id, details, block_id))


def delete_block(block_id):
    with db() as conn:
        conn.execute("DELETE FROM tt_entries WHERE id = ?", (block_id,))


def blocks_between(first, last, track=None):
    """Timetable blocks between two ISO dates (inclusive), ordered by day/track/time."""
    sql = f"SELECT {BLOCK_COLS} FROM tt_entries WHERE date BETWEEN ? AND ?"
    params = [first, last]
    if track:
        sql += " AND track = ?"
        params.append(track)
    rows = _read(sql, params).to_dict("records")
    return sorted(rows, key=lambda r: (r["date"], r["track"], slot_of(r["start"])))


def upsert_blocks(blocks):
    """Save blocks sent by the grid (popover edits). Idempotent."""
    with db() as conn:
        apply_block_ops(conn, [("upsert", b) for b in blocks])


# ---------------------------------------------------------------------------
# Timetable: imported Outlook events
#
# Every sync puts all events on the timetable; the user can then remove an
# occurrence (hidden) or change it, which turns it into their own block
# ("detached": a tt_entries copy with id "ol:<uid>", and the occurrence hidden).
# Nothing is ever written back to Outlook.
# ---------------------------------------------------------------------------

def replace_outlook(rows):
    """Swap in a fresh set of Outlook events (dicts with the tt_outlook columns)."""
    with db() as conn:
        conn.execute("DELETE FROM tt_outlook")
        conn.executemany(
            'INSERT OR REPLACE INTO tt_outlook (uid, date, start, "end", title, location, all_day, exact, description) '
            "VALUES (:uid, :date, :start, :end, :title, :location, :all_day, :exact, :description)",
            [{"description": None, **r} for r in rows],
        )


def outlook_between(first, last):
    """Visible Outlook events between two ISO dates (inclusive), timed ones in time order."""
    rows = _read(f"SELECT {OUTLOOK_COLS} FROM tt_outlook WHERE date BETWEEN ? AND ? "
                 "AND uid NOT IN (SELECT uid FROM tt_outlook_hidden)", (first, last)).to_dict("records")
    return sorted(rows, key=lambda r: (r["date"], r["all_day"] == 0, slot_of(r["start"]) if r["start"] else -1))


OUTLOOK_COLS = 'uid, date, start, "end", title, location, all_day, exact, description'


def _visible_outlook(conn, day, s=0, e=SLOTS):
    """Visible timed Outlook events on a timetable day overlapping slots s..e-1."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT {OUTLOOK_COLS} FROM tt_outlook WHERE date = ? AND all_day = 0 "
        "AND uid NOT IN (SELECT uid FROM tt_outlook_hidden)", (day,)).fetchall()
    conn.row_factory = None
    return [dict(r) for r in rows if slot_of(r["start"]) < e and slot_of(r["end"]) > s]


def outlook_details(row):
    """Location and description of an Outlook event as one short text."""
    parts = [f"Location: {row['location']}" if row.get("location") else None, row.get("description")]
    return "\n".join(p for p in parts if p) or None


def _as_block(row):
    """An Outlook event in the shape of one of the user's blocks (as it would be once detached)."""
    return {"id": f"ol:{row['uid']}", "date": row["date"], "track": "plan", "start": row["start"],
            "end": row["end"], "label": row["title"], "category_id": outlook_category(row["title"]),
            "details": outlook_details(row)}


def _detach(conn, row):
    conn.execute("INSERT OR IGNORE INTO tt_outlook_hidden VALUES (?)", (row["uid"],))
    apply_block_ops(conn, [("upsert", _as_block(row))])
    return f"ol:{row['uid']}"


def get_outlook(uid):
    """One visible Outlook event, or None."""
    with db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(f"SELECT {OUTLOOK_COLS} FROM tt_outlook WHERE uid = ? "
                           "AND uid NOT IN (SELECT uid FROM tt_outlook_hidden)", (uid,)).fetchone()
    return dict(row) if row else None


def hide_outlook(uid):
    """Remove an Outlook occurrence from the timetable (it stays in Outlook)."""
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO tt_outlook_hidden VALUES (?)", (uid,))


def detach_outlook(uid):
    """Turn an Outlook occurrence into the user's own block; returns its block id (or None)."""
    with db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(f"SELECT {OUTLOOK_COLS} FROM tt_outlook WHERE uid = ?", (uid,)).fetchone()
        conn.row_factory = None
        if row is None or row["all_day"]:
            return None
        if get_block_in(conn, f"ol:{uid}"):
            return f"ol:{uid}"                       # already detached
        return _detach(conn, dict(row))


def get_block_in(conn, block_id):
    return conn.execute("SELECT 1 FROM tt_entries WHERE id = ?", (block_id,)).fetchone() is not None


def outlook_signature():
    """A cheap fingerprint of tt_outlook, to tell whether a sync changed anything."""
    with db() as conn:
        return conn.execute(
            'SELECT COUNT(*), TOTAL(LENGTH(uid || date || COALESCE(start, \'\') || COALESCE("end", \'\') || title)), '
            "GROUP_CONCAT(uid || COALESCE(start, '') || title, '|') FROM tt_outlook"
        ).fetchone()


def outlook_category(title):
    """
    Category for Outlook events: settings.json "outlook_category" (set by asking Claude),
    else the default, else the first category. (The title is kept for future rules.)
    """
    wanted = load_settings().get("outlook_category") or DEFAULT_OUTLOOK_CATEGORY
    ids = list(load_categories()["id"])
    return wanted if wanted in ids else (ids[0] if ids else wanted)


def outlook_overlaps(day, s, e):
    """Visible timed Outlook events on that timetable day covering any of slots s..e-1."""
    return [r for r in outlook_between(day, day)
            if not r["all_day"] and slot_of(r["start"]) < e and slot_of(r["end"]) > s]


# ---------------------------------------------------------------------------
# Goals: to-dos for a day, a week (Monday to Sunday), a month or a year.
#
# Each goal belongs to one period, identified by its first day (period_start).
# Repeating goals get a fresh unticked copy when a new period starts (roll_goals).
# "Today" is always the Europe/London date.
# ---------------------------------------------------------------------------

GOAL_PERIODS = ("daily", "weekly", "monthly", "yearly")
GOAL_COLS = 'id, title, notes, period, period_start, done, done_at, "repeat", ord, created_at'
TZ = ZoneInfo("Europe/London")


def london_today():
    return datetime.now(TZ).date()


def _as_date(day):
    return day if isinstance(day, date) else date.fromisoformat(str(day))


def goal_period_start(period, day):
    """First day of the period containing `day`: the day, its Monday, the 1st, or 1 Jan."""
    day = _as_date(day)
    if period == "daily":
        return day
    if period == "weekly":
        return day - timedelta(days=day.weekday())
    if period == "monthly":
        return day.replace(day=1)
    if period == "yearly":
        return day.replace(month=1, day=1)
    raise ValueError(f"Unknown goal period: {period}")


def shift_period(period, start, n):
    """The start of the period n periods after (or before, n < 0) the one starting at `start`."""
    start = goal_period_start(period, start)
    if period == "daily":
        return start + timedelta(days=n)
    if period == "weekly":
        return start + timedelta(weeks=n)
    if period == "monthly":
        months = start.year * 12 + start.month - 1 + n
        return date(months // 12, months % 12 + 1, 1)
    return start.replace(year=start.year + n)


def goal_period_end(period, start):
    """Last day of the period starting at `start`."""
    return shift_period(period, start, 1) - timedelta(days=1)


def _goal(row):
    g = dict(row)
    g["done"], g["repeat"] = bool(g["done"]), bool(g["repeat"])
    return g


def _short(d, year=False):
    return f"{d.day} {d:%b}" + (f" {d.year}" if year else "")


def period_title(period, start, today=None):
    """
    A period's name relative to today: "Today", "Fri 30 Oct", "This week: 26 Oct to 1 Nov",
    "Last week: ...", "19 to 25 Oct", "November 2026", "2026".
    """
    today = today or london_today()
    start = goal_period_start(period, start)
    if period == "daily":
        named = {0: "Today", -1: "Yesterday", 1: "Tomorrow"}.get((start - today).days)
        return named or f"{start:%a} {start.day} {start:%b}" + (f" {start.year}" if start.year != today.year else "")
    if period == "weekly":
        end = goal_period_end(period, start)
        other_year = end.year != today.year
        span = (f"{start.day} to {_short(end, other_year)}" if start.month == end.month
                else f"{_short(start, start.year != end.year)} to {_short(end, other_year)}")
        weeks = (start - goal_period_start("weekly", today)).days // 7
        named = {0: "This week", -1: "Last week", 1: "Next week"}.get(weeks)
        return f"{named}: {span}" if named else span
    if period == "monthly":
        return f"{start:%B %Y}"
    return str(start.year)


def goals_for(period, start):
    """The goals of one period, in list order."""
    start = goal_period_start(period, start).isoformat()
    with db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {GOAL_COLS} FROM goals WHERE period = ? AND period_start = ? ORDER BY ord, created_at",
            (period, start),
        ).fetchall()
    return [_goal(r) for r in rows]


def get_goal(goal_id):
    with db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(f"SELECT {GOAL_COLS} FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return _goal(row) if row else None


def _next_ord(conn, period, start):
    return conn.execute("SELECT COALESCE(MAX(ord) + 1, 0) FROM goals WHERE period = ? AND period_start = ?",
                        (period, start)).fetchone()[0]


def _insert_goal(conn, title, period, start, notes=None, repeat=False, goal_id=None):
    """Add a goal at the end of its list. Idempotent for a given id (the goal list replays ops)."""
    goal_id = goal_id or uuid.uuid4().hex
    conn.execute(
        f"INSERT OR IGNORE INTO goals ({GOAL_COLS}) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, ?)",
        (goal_id, title, notes, period, start, int(bool(repeat)), _next_ord(conn, period, start),
         datetime.now(TZ).isoformat(timespec="seconds")),
    )
    return goal_id


def add_goal(title, period, day, notes=None, repeat=False, goal_id=None):
    """Add a goal to the period containing `day`; returns it."""
    title = str(title).strip()
    if not title:
        raise ValueError("A goal needs a title.")
    start = goal_period_start(period, day).isoformat()
    with db() as conn:
        goal_id = _insert_goal(conn, title, period, start, notes or None, repeat, goal_id)
    return get_goal(goal_id)


def set_goal_done(goal_id, done):
    """Tick or untick a goal (done_at records when it was ticked)."""
    with db() as conn:
        conn.execute(
            "UPDATE goals SET done = ?, done_at = CASE WHEN ? THEN COALESCE(done_at, ?) END WHERE id = ?",
            (int(bool(done)), int(bool(done)), datetime.now(TZ).isoformat(timespec="seconds"), goal_id),
        )


def update_goal(goal_id, **changes):
    """Change a goal's title, notes and/or repeat flag."""
    fields = {k: v for k, v in changes.items() if k in ("title", "notes", "repeat")}
    if "title" in fields:
        fields["title"] = str(fields["title"]).strip()
        if not fields["title"]:
            raise ValueError("A goal needs a title.")
    if "notes" in fields:
        fields["notes"] = (str(fields["notes"]).strip() if fields["notes"] is not None else "") or None
    if "repeat" in fields:
        fields["repeat"] = int(bool(fields["repeat"]))
    if not fields:
        return
    sets = ", ".join(f'"{k}" = ?' for k in fields)
    with db() as conn:
        conn.execute(f"UPDATE goals SET {sets} WHERE id = ?", (*fields.values(), goal_id))


def move_target(goal, period, today=None):
    """
    Where a goal lands when moved to another kind of period: the period containing
    today if its current period includes today, otherwise its own first day.
    """
    today = today or london_today()
    start = date.fromisoformat(goal["period_start"])
    anchor = today if start <= today <= goal_period_end(goal["period"], start) else start
    return goal_period_start(period, anchor)


def move_goal(goal_id, period, today=None):
    """Move a goal to another kind of period (to the end of that list). Returns the new period start."""
    goal = get_goal(goal_id)
    if not goal:
        return None
    start = move_target(goal, period, today).isoformat()
    with db() as conn:
        conn.execute("UPDATE goals SET period = ?, period_start = ?, ord = ? WHERE id = ?",
                     (period, start, _next_ord(conn, period, start), goal_id))
    return start


def delete_goal(goal_id):
    with db() as conn:
        conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))


def reorder_goals(ids):
    """Put goals in this order (ids of one list, first to last)."""
    with db() as conn:
        conn.executemany("UPDATE goals SET ord = ? WHERE id = ?", [(i, gid) for i, gid in enumerate(ids)])


def _titles(conn, period, start):
    rows = conn.execute("SELECT lower(title) FROM goals WHERE period = ? AND period_start = ?", (period, start))
    return {r[0] for r in rows}


def roll_goals(today=None):
    """
    Give every repeating goal an unticked copy in the current period, once per
    period (goal_rollover remembers the last one done, so a copy the user deletes
    stays deleted). A goal repeats if the latest goal with its title in an earlier
    period has repeat on. Titles already in the current period are never doubled.
    Returns how many copies were made.
    """
    today = today or london_today()
    made = 0
    with db() as conn:
        for period in GOAL_PERIODS:
            start = goal_period_start(period, today).isoformat()
            done = conn.execute("SELECT period_start FROM goal_rollover WHERE period = ?", (period,)).fetchone()
            if done and done[0] >= start:
                continue
            latest = {}
            for title, notes, repeat in conn.execute(
                'SELECT title, notes, "repeat" FROM goals WHERE period = ? AND period_start < ? '
                "ORDER BY period_start DESC, ord",
                (period, start),
            ):
                latest.setdefault(title.lower(), (title, notes, repeat))
            existing = _titles(conn, period, start)
            for key, (title, notes, repeat) in latest.items():
                if repeat and key not in existing:
                    _insert_goal(conn, title, period, start, notes, True)
                    made += 1
            conn.execute("INSERT OR REPLACE INTO goal_rollover VALUES (?, ?)", (period, start))
    return made


def carry_over(period, start):
    """Copy a period's unticked goals into the next period (skipping titles already there). Returns the count."""
    start = goal_period_start(period, start).isoformat()
    nxt = shift_period(period, start, 1).isoformat()
    copied = 0
    with db() as conn:
        existing = _titles(conn, period, nxt)
        rows = conn.execute(
            'SELECT title, notes, "repeat" FROM goals WHERE period = ? AND period_start = ? AND done = 0 ORDER BY ord',
            (period, start),
        ).fetchall()
        for title, notes, repeat in rows:
            if title.lower() not in existing:
                _insert_goal(conn, title, period, nxt, notes, repeat)
                existing.add(title.lower())
                copied += 1
    return copied


def goals_progress(period, start):
    """(done, total) for one period."""
    goals = goals_for(period, start)
    return sum(g["done"] for g in goals), len(goals)


def load_goals():
    """Every goal, for export: one row each, oldest period first."""
    return _read(f"SELECT {GOAL_COLS} FROM goals ORDER BY period_start, period, ord")


def save_goals(df, replace=False):
    """
    Import goals (the export's columns; only title and period are required).
    Rows with an id that already exists update that goal; replace=True wipes all goals first.
    Returns how many rows were imported.
    """
    count = 0
    with db() as conn:
        if replace:
            conn.execute("DELETE FROM goals")
            conn.execute("DELETE FROM goal_rollover")
        for row in df.to_dict("records"):
            title = _clean(row.get("title"), str)
            period = (_clean(row.get("period"), str) or "").strip().lower()
            if not title or period not in GOAL_PERIODS:
                continue
            day = _clean(row.get("period_start"), str)
            start = goal_period_start(period, pd.to_datetime(day).date() if day else london_today()).isoformat()
            done = _is_ticked(row.get("done"))
            ord_ = _clean(row.get("ord"), int)
            conn.execute(
                f"INSERT OR REPLACE INTO goals ({GOAL_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_clean(row.get("id"), str) or uuid.uuid4().hex, title.strip(), _clean(row.get("notes"), str),
                 period, start, int(done), _clean(row.get("done_at"), str) if done else None,
                 int(_is_ticked(row.get("repeat"))),
                 _next_ord(conn, period, start) if ord_ is None else ord_,
                 _clean(row.get("created_at"), str) or datetime.now(TZ).isoformat(timespec="seconds")),
            )
            count += 1
    return count


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def period_start(days, today=None):
    """First day of a period of `days` days ending today; None = since the first record."""
    today = today or date.today()
    if days:
        return today - timedelta(days=days - 1)
    with db() as conn:
        first = conn.execute(
            "SELECT MIN(d) FROM (SELECT MIN(date) d FROM entries UNION SELECT MIN(date) FROM habit_log"
            " UNION SELECT MIN(date) FROM tt_entries)"
        ).fetchone()[0]
    return date.fromisoformat(first) if first else today


def get_stats(days=None, today=None):
    """
    Summary for the last `days` days (None = all time): averages, habit
    completion %, and timetable hours per category for plan and actual.
    """
    today = today or date.today()
    start = period_start(days, today)
    first, last = start.isoformat(), today.isoformat()
    num_days = (today - start).days + 1

    entries = entries_between(first, last)

    def avg(col):
        vals = [v for v in entries[col] if v is not None] if len(entries) else []
        return round(sum(vals) / len(vals), 2) if vals else None

    with db() as conn:
        counts = dict(conn.execute(
            "SELECT habit_id, COUNT(*) FROM habit_log WHERE date BETWEEN ? AND ? GROUP BY habit_id",
            (first, last),
        ).fetchall())
    habits = {name: round(100 * counts.get(hid, 0) / num_days, 1) for hid, name in get_habits()}

    cats = load_categories()
    names = dict(zip(cats["id"], cats["name"]))
    hours = {"plan": {}, "actual": {}}
    for b in blocks_between(first, last):
        name = names.get(b["category_id"], "Uncategorised")
        h = (slot_of(b["end"]) - slot_of(b["start"])) / 2
        hours[b["track"]][name] = hours[b["track"]].get(name, 0) + h

    return {
        "start": first, "end": last, "days": num_days,
        "days_logged": len(entries),
        "avg_mood": avg("mood"), "avg_sleep": avg("sleep"), "avg_energy": avg("energy"),
        "habit_completion_pct": habits,
        "timetable_hours": hours,
    }
