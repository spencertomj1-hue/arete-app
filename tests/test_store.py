"""The shared timetable painting rules (store.plan_paint) and other store helpers."""

from datetime import date


def blocks(store, day="2026-10-26", track="plan"):
    return [(b["start"], b["end"], b["label"]) for b in store.blocks_between(day, day, track)]


def test_slots_wrap_past_midnight(store):
    assert store.slot_time(0) == "07:00"
    assert store.slot_time(34) == "00:00"
    assert store.slot_time(36) == "01:00"
    assert store.slot_of("00:30") == 35
    assert store.slot_of("01:00") == 36


def test_paint_creates_block(store):
    store.paint("2026-10-26", "plan", 4, 8, {"label": "Lecture", "category_id": "classes"})
    assert blocks(store) == [("09:00", "11:00", "Lecture")]


def test_paint_splits_overlapped_block(store):
    store.paint("2026-10-26", "plan", 0, 6, {"label": "Sleep", "category_id": "sleep"})
    affected = store.paint("2026-10-26", "plan", 2, 3, {"label": "Lunch", "category_id": "meal"})
    assert [a["label"] for a in affected] == ["Sleep"]
    assert blocks(store) == [("07:00", "08:00", "Sleep"), ("08:00", "08:30", "Lunch"), ("08:30", "10:00", "Sleep")]


def test_paint_trims_and_removes(store):
    store.paint("2026-10-26", "plan", 0, 2, {"label": "Sleep", "category_id": "sleep"})
    store.paint("2026-10-26", "plan", 2, 4, {"label": "Gym", "category_id": "gym"})
    store.paint("2026-10-26", "plan", 1, 4, {"label": "Rest", "category_id": "rest"})
    assert blocks(store) == [("07:00", "07:30", "Sleep"), ("07:30", "09:00", "Rest")]


def test_paint_merges_touching_identical_blocks(store):
    store.paint("2026-10-26", "plan", 0, 2, {"label": "Sleep", "category_id": "sleep"})
    affected = store.paint("2026-10-26", "plan", 2, 3, {"label": "Sleep", "category_id": "sleep"})
    assert affected == []                     # extending isn't overwriting
    assert blocks(store) == [("07:00", "08:30", "Sleep")]


def test_painting_inside_identical_block_keeps_one_block(store):
    store.paint("2026-10-26", "plan", 0, 6, {"label": "Sleep", "category_id": "sleep"})
    affected = store.paint("2026-10-26", "plan", 2, 3, {"label": "Sleep", "category_id": "sleep"})
    assert affected == []
    assert blocks(store) == [("07:00", "10:00", "Sleep")]


def test_erase_and_tracks_are_independent(store):
    store.paint("2026-10-26", "plan", 0, 4, {"label": "Sleep", "category_id": "sleep"})
    store.paint("2026-10-26", "actual", 0, 4, {"label": "Sleep", "category_id": "sleep"})
    store.paint("2026-10-26", "plan", 1, 3, None)
    assert blocks(store) == [("07:00", "07:30", "Sleep"), ("08:30", "09:00", "Sleep")]
    assert blocks(store, track="actual") == [("07:00", "09:00", "Sleep")]


def test_after_midnight_block(store):
    store.paint("2026-10-26", "plan", 34, 36, {"label": "Sleep", "category_id": "sleep"})
    assert blocks(store) == [("00:00", "01:00", "Sleep")]


def test_new_id_is_used(store):
    store.paint("2026-10-26", "plan", 0, 1, {"label": "Sleep", "category_id": "sleep"}, new_id="abc")
    assert store.get_block("abc")["label"] == "Sleep"


def test_resolve_category(store):
    assert store.resolve_category(label="lecture") == "classes"
    assert store.resolve_category(label="Revision") == "study"
    assert store.resolve_category(label="Exercise") == "exercise"   # category name match
    assert store.resolve_category(category="Classes") == "classes"
    assert store.resolve_category(category="meals") == "meals"
    assert store.resolve_category(label="Something new") is None


def test_stats(store):
    with store.db() as conn:
        store.save_entry(conn, "2026-10-28", 4, 8.0, 3, None)
        store.save_entry(conn, "2026-10-29", 2, 6.0, 5, "tired")
    store.add_habit("Read")
    hid = store.find_habit("read")[0]
    with store.db() as conn:
        store.set_habit_done(conn, hid, "2026-10-29", True)
    store.paint("2026-10-29", "actual", 4, 8, {"label": "Lecture", "category_id": "classes"})
    s = store.get_stats(7, today=date(2026, 10, 29))
    assert s["avg_mood"] == 3 and s["avg_sleep"] == 7 and s["avg_energy"] == 4
    assert s["habit_completion_pct"] == {"Read": round(100 / 7, 1)}
    assert s["timetable_hours"]["actual"] == {"Classes": 2.0}


def test_water_import_export(store):
    import pandas as pd
    with store.db() as conn:
        store.save_entry(conn, "2026-10-28", 3, 7.0, 3, None, 2.25)
        store.save_entry(conn, "2026-10-29", 4, 8.0, 4, "ok", 1.0)
        store.save_entry(conn, "2026-10-29", 5, 8.0, 4, "ok")            # water not given: kept
    assert store.get_entry("2026-10-29")["water"] == 1.0
    df = store.load_all_data()
    assert dict(zip(df["date"].astype(str), df["water"])) == {"2026-10-29": 1.0, "2026-10-28": 2.25}
    store.save_all_data(pd.DataFrame({"date": ["2026-10-29"], "mood": [2]}))  # old CSV without water: kept
    assert store.get_entry("2026-10-29")["water"] == 1.0
    store.save_all_data(pd.DataFrame({"date": ["2026-10-30"], "water": [3]}))
    assert store.get_entry("2026-10-30")["water"] == 3.0
    assert store.get_stats(7, today=__import__("datetime").date(2026, 10, 30))["avg_water"] == 2.08
