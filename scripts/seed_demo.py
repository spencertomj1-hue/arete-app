"""
Create a demo data folder full of realistic fake data (for screenshots and trying
Arete out). It never touches your real data: it always writes to a new, empty folder.

    .venv/bin/python scripts/seed_demo.py              # a new temp folder
    .venv/bin/python scripts/seed_demo.py /tmp/demo    # a folder of your choice (must be empty)

Then run the app on it:

    ARETE_DATA_DIR=<folder> .venv/bin/streamlit run app.py
"""

import os
import random
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arete.system import default_data_dir  # noqa: E402

REAL_DATA = default_data_dir()


def pick_folder():
    folder = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="arete-demo-"))
    if folder == REAL_DATA.resolve():
        sys.exit("That's your real Arete data folder. Pick another folder.")
    if folder.exists() and any(folder.iterdir()):
        sys.exit(f"{folder} isn't empty. Pick an empty or new folder.")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


FOLDER = pick_folder()
os.environ["ARETE_DATA_DIR"] = str(FOLDER)          # before importing store
os.environ.pop("ARETE_DB", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arete import store  # noqa: E402  (needs ARETE_DATA_DIR set first)

random.seed(7)
TODAY = date.today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())

NOTES = [
    "Good focus in the morning, slow afternoon.", "Long walk by the river after dinner.",
    "Tired after a late night, but the run helped.", "Finished the project draft!",
    "Rainy day, stayed in and read.", "Caught up with friends over pizza.",
    "Too much time on my phone tonight.", "Great gym session, new personal best.",
    None, None, None,
]

# A typical weekday plan, and how it actually went (some drift, like real life).
WEEKDAY_PLAN = [
    ("07:00", "07:30", "Breakfast", "meals"), ("08:00", "08:30", "Commute", "commute"),
    ("09:00", "11:00", "Deep work", "work"), ("11:00", "12:30", "Study", "study"),
    ("12:30", "13:30", "Lunch", "meals"), ("14:00", "16:00", "Project", "projects"),
    ("17:30", "18:30", "Gym", "exercise"), ("19:00", "20:00", "Dinner", "meals"),
    ("20:30", "21:30", "Reading", "reading"), ("23:30", "01:00", "Sleep", "sleep"),
]
WEEKEND_PLAN = [
    ("09:00", "10:00", "Breakfast", "meals"), ("10:30", "12:00", "Run", "exercise"),
    ("13:00", "14:00", "Lunch", "meals"), ("15:00", "18:00", "Friends", "social"),
    ("19:00", "20:00", "Dinner", "meals"), ("21:00", "22:00", "Reading", "reading"),
]


def shifted(t, minutes):
    h, m = map(int, t.split(":"))
    total = h * 60 + m + minutes
    total = max(7 * 60, min(total, 24 * 60 + 60))       # stay on the 07:00-01:00 grid
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


def main():
    store.init_db()                                     # generic categories and activity defaults
    store.save_settings(wizard_done=True)

    # --- Timetable: this week, plan and actual, up to today ---
    for i in range(7):
        day = MONDAY + timedelta(days=i)
        plan = WEEKDAY_PLAN if day.weekday() < 5 else WEEKEND_PLAN
        for start, end, label, cat in plan:
            store.paint(day.isoformat(), "plan", store.slot_of(start), store.slot_of(end),
                        {"label": label, "category_id": cat})
            if day > TODAY:
                continue
            if random.random() < 0.15:                  # skipped it...
                if random.random() < 0.6:
                    store.paint(day.isoformat(), "actual", store.slot_of(start), store.slot_of(end),
                                {"label": "Phone", "category_id": "distracted"})
                continue
            drift = random.choice([0, 0, 0, 30, -30])
            s, e = store.slot_of(shifted(start, drift)), store.slot_of(shifted(end, drift))
            if e > s:
                store.paint(day.isoformat(), "actual", s, e, {"label": label, "category_id": cat})

    # --- A few fake "Outlook" events (imported calendar) ---
    rows = []
    for i, (title, start, end, where) in enumerate([
        ("Chemistry 101", "09:00", "10:00", "Room 1.04"), ("Team stand-up", "10:00", "10:30", "Online"),
        ("Seminar: Design basics", "15:00", "16:00", "Hall B"),
    ]):
        for d in (0, 2, 4):
            day = (MONDAY + timedelta(days=d)).isoformat()
            if title == "Team stand-up" and d != 2:
                continue
            rows.append({"uid": f"demo-{i}-{d}", "date": day, "start": start, "end": end, "title": title,
                         "location": where, "all_day": 0, "exact": f"{start}–{end}",
                         "description": "Demo event (not a real calendar)."})
    rows.append({"uid": "demo-holiday", "date": (MONDAY + timedelta(days=4)).isoformat(), "start": None,
                 "end": None, "title": "Bank holiday", "location": None, "all_day": 1, "exact": None,
                 "description": None})
    store.replace_outlook(rows)

    # --- Daily log: the last 30 days ---
    with store.db() as conn:
        for back in range(30):
            day = TODAY - timedelta(days=back)
            if random.random() < 0.1:
                continue                                # a few days not logged
            mood = random.choice([2, 3, 3, 4, 4, 4, 5])
            sleep = random.choice([6.0, 6.5, 7.0, 7.0, 7.5, 8.0, 8.5])
            energy = max(1, min(5, mood + random.choice([-1, 0, 0, 1])))
            store.save_entry(conn, day.isoformat(), mood, sleep, energy, random.choice(NOTES))

    # --- Habits with streaks ---
    for name, rate in (("Read 20 pages", 0.8), ("Exercise", 0.6), ("Meditate", 0.5), ("No phone in bed", 0.4)):
        store.add_habit(name)
        habit_id = store.find_habit(name)[0]
        with store.db() as conn:
            for back in range(30):
                if back < 5 and name == "Read 20 pages" or random.random() < rate:   # a current streak
                    store.set_habit_done(conn, habit_id, (TODAY - timedelta(days=back)).isoformat(), True)

    print(f"Demo data created in: {FOLDER}")
    print(f"Run the app on it:    ARETE_DATA_DIR='{FOLDER}' .venv/bin/streamlit run app.py")


if __name__ == "__main__":
    main()
