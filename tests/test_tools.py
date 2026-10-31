"""The assistant's tools (tools.py), run against a throwaway database."""

import importlib
from datetime import date

import pytest


@pytest.fixture
def tools(store):
    from arete import tools as tools_module
    return importlib.reload(tools_module)   # picks up the reloaded store


DAY = "2026-10-29"


def blocks(store, track="plan", day=DAY):
    return [(b["start"], b["end"], b["label"], b["category_id"]) for b in store.blocks_between(day, day, track)]


# --- log_day ---

def test_log_day_new_entry_saves_straight_away(tools, store):
    r = tools.log_day(DAY, mood=4, sleep=7.5)
    assert r.changed and r.pending is None
    assert store.get_entry(DAY)["mood"] == 4 and store.get_entry(DAY)["sleep"] == 7.5


def test_log_day_keeps_untouched_fields(tools, store):
    tools.log_day(DAY, mood=4, sleep=7.5, notes="ok")
    tools.log_day(DAY, energy=3)
    e = store.get_entry(DAY)
    assert (e["mood"], e["sleep"], e["energy"], e["notes"]) == (4, 7.5, 3, "ok")


def test_log_day_overwrite_needs_confirm(tools, store):
    tools.log_day(DAY, mood=4)
    r = tools.log_day(DAY, mood=2)
    assert r.pending and "mood 4 → 2" in r.pending.summary
    assert store.get_entry(DAY)["mood"] == 4           # not applied yet
    done = r.pending.run()
    assert done.changed and store.get_entry(DAY)["mood"] == 2


def test_log_day_append_notes_needs_no_confirm(tools, store):
    tools.log_day(DAY, notes="Gym")
    r = tools.log_day(DAY, notes="Cinema", append_notes=True)
    assert r.pending is None
    assert store.get_entry(DAY)["notes"] == "Gym\nCinema"


def test_log_day_replacing_notes_needs_confirm(tools, store):
    tools.log_day(DAY, notes="Gym")
    assert tools.log_day(DAY, notes="Cinema").pending


def test_log_day_validation(tools):
    with pytest.raises(tools.ToolError):
        tools.log_day(DAY, mood=7)
    with pytest.raises(tools.ToolError):
        tools.log_day("tomorrow", mood=3)
    with pytest.raises(tools.ToolError):
        tools.log_day(DAY)


# --- habits ---

def test_habits(tools, store):
    assert tools.add_habit("Read").changed
    assert "already exists" in tools.add_habit("read").text
    r = tools.set_habit(DAY, "READ", True)
    assert r.changed and DAY in store.get_habit_dates(store.find_habit("Read")[0])
    assert "already ticked" in tools.set_habit(DAY, "Read", True).text
    # Unticking deletes data, so it needs a confirm.
    r = tools.set_habit(DAY, "Read", False)
    assert r.pending and DAY in store.get_habit_dates(store.find_habit("Read")[0])
    r.pending.run()
    assert DAY not in store.get_habit_dates(store.find_habit("Read")[0])


def test_unknown_habit_is_an_error(tools):
    with pytest.raises(tools.ToolError, match="Ask the user"):
        tools.set_habit(DAY, "Meditate", True)


def test_list_habits(tools):
    tools.add_habit("Read")
    tools.set_habit("2026-10-28", "Read", True)
    tools.set_habit("2026-10-29", "Read", True)
    assert "current streak 2" in tools.list_habits(today=date(2026, 10, 29)).text


# --- timetable ---

def test_add_block_picks_category_from_label_map(tools, store):
    r = tools.add_block(DAY, "plan", "09:00", "11:00", "Lecture")
    assert r.changed and r.pending is None
    assert blocks(store) == [("09:00", "11:00", "Lecture", "classes")]


def test_add_block_explicit_category_name(tools, store):
    tools.add_block(DAY, "actual", "18:00", "20:00", "Five-a-side", category="exercise")
    assert blocks(store, "actual") == [("18:00", "20:00", "Five-a-side", "exercise")]


def test_add_block_unknown_category_asks(tools):
    with pytest.raises(tools.ToolError, match="Ask the user"):
        tools.add_block(DAY, "plan", "09:00", "10:00", "Piano")


def test_add_block_overlap_needs_confirm_and_splits(tools, store):
    tools.add_block(DAY, "plan", "07:00", "10:00", "Sleep")
    r = tools.add_block(DAY, "plan", "08:00", "08:30", "Breakfast", category="Meals")
    assert r.pending and "Sleep" in r.pending.summary
    assert blocks(store) == [("07:00", "10:00", "Sleep", "sleep")]       # untouched until confirmed
    r.pending.run()
    assert blocks(store) == [("07:00", "08:00", "Sleep", "sleep"), ("08:00", "08:30", "Breakfast", "meals"),
                             ("08:30", "10:00", "Sleep", "sleep")]


def test_add_block_extending_same_block_needs_no_confirm(tools, store):
    tools.add_block(DAY, "plan", "07:00", "08:00", "Sleep")
    r = tools.add_block(DAY, "plan", "08:00", "09:00", "Sleep")
    assert r.pending is None
    assert blocks(store) == [("07:00", "09:00", "Sleep", "sleep")]


def test_add_block_after_midnight_and_time_rules(tools, store):
    tools.add_block(DAY, "actual", "23:30", "01:00", "Sleep")
    assert blocks(store, "actual") == [("23:30", "01:00", "Sleep", "sleep")]
    for start, end in (("06:00", "07:00"), ("09:15", "10:00"), ("10:00", "09:00"), ("00:30", "01:30")):
        with pytest.raises(tools.ToolError):
            tools.add_block(DAY, "plan", start, end, "Sleep")
    with pytest.raises(tools.ToolError):
        tools.add_block(DAY, "maybe", "09:00", "10:00", "Sleep")


def test_update_block_relabel_and_move(tools, store):
    tools.add_block(DAY, "plan", "09:00", "11:00", "Lecture")
    tools.add_block(DAY, "plan", "12:00", "13:00", "Lunch")
    bid = store.blocks_between(DAY, DAY, "plan")[0]["id"]

    r = tools.update_block(bid, label="Homework")
    assert r.pending                                   # always previewed
    r.pending.run()
    assert blocks(store)[0] == ("09:00", "11:00", "Homework", "study")   # label map picked Study

    r = tools.update_block(bid, start="11:30", end="12:30")
    assert "overwrites" in r.pending.summary and "Lunch" in r.pending.summary
    r.pending.run()
    assert blocks(store) == [("11:30", "12:30", "Homework", "study"), ("12:30", "13:00", "Lunch", "meals")]


def test_delete_block(tools, store):
    tools.add_block(DAY, "plan", "09:00", "11:00", "Lecture")
    bid = store.blocks_between(DAY, DAY)[0]["id"]
    r = tools.delete_block(bid)
    assert r.pending and blocks(store)
    r.pending.run()
    assert blocks(store) == []
    with pytest.raises(tools.ToolError):
        tools.delete_block(bid)


def test_queries(tools):
    tools.add_block(DAY, "plan", "09:00", "11:00", "Lecture")
    tools.log_day(DAY, mood=4, notes="good")
    tools.add_habit("Read")
    tools.set_habit(DAY, "Read", True)
    assert "Lecture (Classes)" in tools.query_blocks("2026-10-26", "2026-11-01").text
    assert "No timetable blocks" in tools.query_blocks("2026-10-26", "2026-11-01", track="actual").text
    q = tools.query_entries("2026-10-26", "2026-11-01").text
    assert "mood 4" in q and "habits: Read" in q
    s = tools.get_stats("7d", today=date(2026, 10, 29)).text
    assert "mood 4.0" in s and "Classes 2h" in s
    assert "Life:" in tools.list_categories().text
    with pytest.raises(tools.ToolError):
        tools.get_stats("fortnight")


def test_edit_category_and_activity_default(tools, store):
    assert tools.edit_category("exercise", colour="#1b7f3b").changed
    cats = {r["name"]: r for r in store.load_categories().to_dict("records")}
    assert cats["Exercise"]["colour"] == "#1B7F3B"
    assert tools.edit_category("Exercise", new_name="Workout").changed
    assert "Workout" in set(store.load_categories()["name"])
    with pytest.raises(tools.ToolError, match="hex"):
        tools.edit_category("Workout", colour="green")
    with pytest.raises(tools.ToolError, match="create=true"):
        tools.edit_category("Piano")
    assert tools.edit_category("Piano", colour="#AA00AA", group="life", create=True).changed
    assert store.resolve_category(category="Piano") == "piano"

    assert tools.set_activity_default("Scales", "Piano").changed
    assert store.resolve_category(label="scales") == "piano"
    tools.add_block("2026-10-29", "plan", "18:00", "19:00", "Scales")
    assert store.blocks_between("2026-10-29", "2026-10-29")[0]["category_id"] == "piano"


# --- to-dos ---

@pytest.fixture
def friday(store, monkeypatch):
    """Pin "today" (Europe/London) to Friday 30 Oct 2026."""
    monkeypatch.setattr(store, "london_today", lambda: date(2026, 10, 30))


def test_add_todo_to_this_week(tools, store, friday):
    r = tools.add_todo("Go gym", "weekly")
    assert r.changed and r.text == "Added 'Go gym' to this week's to-dos (26 Oct to 1 Nov)."
    assert [g["title"] for g in store.todos_for("weekly", "2026-10-26")] == ["Go gym"]
    again = tools.add_todo("go gym", "week")
    assert not again.changed and "already" in again.text
    r = tools.add_todo("Budget", "monthly", date="2026-11-12", repeat=True, notes="bills")
    assert "the November 2026 to-dos" in r.text and "repeating every month" in r.text
    assert store.todos_for("monthly", "2026-11-01")[0]["repeat"] is True
    with pytest.raises(tools.ToolError):
        tools.add_todo("x", "fortnightly")


def test_complete_and_uncomplete_by_title(tools, store, friday):
    store.add_todo("Finish problem sheet", "daily", "2026-10-30")
    store.add_todo("Read", "weekly", "2026-10-30")
    r = tools.complete_todo("finish problem sheet")
    assert r.changed and r.text == "Ticked off 'Finish problem sheet' in today's to-dos (1/1 done)."
    assert store.todos_for("daily", "2026-10-30")[0]["done"]
    assert "already" in tools.complete_todo("problem sheet").text      # partial titles work too
    assert tools.uncomplete_todo("Finish problem sheet").changed
    assert not store.todos_for("daily", "2026-10-30")[0]["done"]


def test_find_todo_ambiguous_missing_and_by_period(tools, store, friday):
    store.add_todo("Read", "daily", "2026-10-30")
    store.add_todo("Read", "weekly", "2026-10-30")
    store.add_todo("Old", "daily", "2026-10-01")
    with pytest.raises(tools.ToolError, match="More than one"):
        tools.complete_todo("read")
    assert "this week's" in tools.complete_todo("read", period="weekly").text
    with pytest.raises(tools.ToolError, match="no to-do"):
        tools.complete_todo("old")                                    # not in the current periods
    assert tools.complete_todo("old", date="2026-10-01").changed
    gid = store.todos_for("daily", "2026-10-30")[0]["id"]
    assert tools.complete_todo(gid).changed                           # by id


def test_delete_todo_needs_confirm(tools, store, friday):
    store.add_todo("Tidy desk", "daily", "2026-10-30")
    r = tools.delete_todo("tidy desk")
    assert r.pending and r.pending.summary == "Delete the to-do 'Tidy desk' in today's to-dos"
    assert store.todos_for("daily", "2026-10-30")
    assert r.pending.run().changed
    assert store.todos_for("daily", "2026-10-30") == []


def test_list_todos(tools, store, friday):
    g = store.add_todo("Go gym", "weekly", "2026-10-30", repeat=True, notes="3 times")
    store.set_todo_done(g["id"], True)
    store.add_todo("Essay", "weekly", "2026-10-30")
    text = tools.list_todos(period="weekly").text
    assert text.splitlines() == [
        "This week's to-dos (26 Oct to 1 Nov): 1/2 done",
        f"- [x] Go gym (id {g['id']}; repeats; notes: 3 times)",
        f"- [ ] Essay (id {store.todos_for('weekly', '2026-10-26')[1]['id']})",
    ]
    everything = tools.list_todos().text
    assert "Today's to-dos: 0/0 done" in everything and "The October 2026 to-dos: 0/0 done" in everything


def test_todo_tools_roll_repeats_first(tools, store, friday):
    store.add_todo("Stretch", "daily", "2026-10-29", repeat=True)
    assert "Stretch" in tools.list_todos(period="daily").text
    assert tools.complete_todo("stretch").changed


def test_complete_todo_by_shared_words(tools, store, friday):
    store.add_todo("Problem sheet 4", "daily", "2026-10-30")
    store.add_todo("Go gym", "weekly", "2026-10-30")
    assert "Problem sheet 4" in tools.complete_todo("finish problem sheet").text
    assert store.todos_for("daily", "2026-10-30")[0]["done"]


# --- goals ---

def test_add_and_update_goal_by_hand(tools, store, friday):
    r = tools.add_goal("Read books", 20, "yearly", unit="books")
    assert r.changed and r.text == "Added the goal 'Read books' (2026: 0/20 books)."
    assert "already" in tools.add_goal("read books", 5, "year").text
    assert tools.log_goal_progress("read", 2).text == "Updated 'Read books' (2026: 2/20 books)."
    assert tools.set_goal_progress("books", 5).text == "Set 'Read books' (2026: 5/20 books) (was 2)."
    with pytest.raises(tools.ToolError):
        tools.add_goal("Gym", 12, "weekly")                  # goals are monthly or yearly
    with pytest.raises(tools.ToolError):
        tools.add_goal("Gym", 0)


def test_goal_linked_to_habit(tools, store, friday):
    tools.add_habit("Gym")
    r = tools.add_goal("Gym", 12, unit="sessions", habit="gym")
    assert "counted from the habit Gym" in r.text and "October 2026: 0/12" in r.text
    tools.set_habit("2026-10-29", "Gym", True)
    assert "1/12 sessions (8%)" in tools.list_goals("monthly").text
    with pytest.raises(tools.ToolError, match="Tick the habit"):
        tools.log_goal_progress("gym")
    with pytest.raises(tools.ToolError, match="no habit"):
        tools.add_goal("Swim", 4, habit="swimming")
    assert "counted from" not in tools.edit_goal("gym", unlink_habit=True).text
    assert tools.log_goal_progress("gym", 3).changed


def test_edit_list_and_delete_goal(tools, store, friday):
    tools.add_goal("Run", 50, unit="km")
    assert "80 km" in tools.edit_goal("run", target=80).text
    listing = tools.list_goals().text
    assert "Monthly goals (October 2026):" in listing and "Yearly goals (2026): none" in listing
    r = tools.delete_goal("run")
    assert r.pending and r.pending.summary == "Delete the goal 'Run' (October 2026: 0/80 km)"
    assert store.goals_in("monthly", "2026-10-01")
    r.pending.run()
    assert store.goals_in("monthly", "2026-10-01") == []


# --- network ---

def test_add_people_and_connect(tools, store):
    r = tools.add_person("Sam", "Friend from uni", "Google", "friends")
    assert r.changed and "Sam (Friends, works for Google)" in r.text
    assert "already someone called Sam" in tools.add_person("sam").text
    tools.add_person("Alex Jones", group="Work")
    assert tools.connect_people("sam", "alex", "Football").text == "Connected Sam and Alex Jones (Football)."
    assert tools.connect_people("Alex", "Sam", "Five-a-side").changed           # updates the reason
    text = tools.find_people().text
    assert "Knows Alex Jones (Five-a-side)" in text and text.startswith("Groups: Family, Friends")
    found = tools.find_people("Jones").text.splitlines()[1:]
    assert len(found) == 1 and "Alex Jones" in found[0]                         # only Alex matches
    with pytest.raises(tools.ToolError, match="no group"):
        tools.add_person("Kim", group="Rowing")
    assert tools.add_group("Rowing", colour="#123abc").changed
    assert "Rowing" in tools.add_person("Kim", group="rowing").text
    with pytest.raises(tools.ToolError, match="no one called"):
        tools.connect_people("Sam", "Nobody")
    with pytest.raises(tools.ToolError, match="themselves"):
        tools.connect_people("Sam", "sam")


def test_update_and_ambiguous_people(tools, store):
    tools.add_person("Sam Smith")
    tools.add_person("Sam Lee")
    with pytest.raises(tools.ToolError, match="More than one"):
        tools.update_person("sam", works_for="BBC")
    assert "works for BBC" in tools.update_person("sam lee", works_for="BBC", group="Work").text
    assert "(works for BBC)" in tools.update_person("Sam Lee", clear_group=True).text


def test_disconnect_and_delete_need_confirm(tools, store):
    tools.add_person("Sam")
    tools.add_person("Alex")
    tools.connect_people("Sam", "Alex")
    r = tools.disconnect_people("Sam", "Alex")
    assert r.pending and r.pending.summary == "Remove the connection between Sam and Alex"
    assert store.net_links()
    r.pending.run()
    assert store.net_links() == []
    tools.connect_people("Sam", "Alex")
    r = tools.delete_person("Sam")
    assert r.pending.summary == "Remove Sam from the network, with their 1 connection"
    r.pending.run()
    assert [p["name"] for p in store.net_people()] == ["Alex"] and store.net_links() == []


def test_sub_group_tools(tools, store):
    assert tools.add_group("Northfield", colour="#123456").changed
    assert tools.add_group("Physics Society", inside="northfield").text == "Added the group Northfield › Physics Society."
    with pytest.raises(tools.ToolError, match="no group"):
        tools.add_group("Lab", inside="Oxford")
    tools.add_person("Sam", group="physics society")
    assert "Sam (Northfield › Physics Society)" in tools.find_people().text
    assert "Northfield (Physics Society)" in tools.find_people().text.splitlines()[0]
    assert "Chess" in tools.edit_group("Physics Society", name="Chess Club", top_level=True).text.replace("Chess Club", "Chess")
    with pytest.raises(tools.ToolError, match="inside itself"):
        tools.edit_group("Northfield", inside="Northfield")
    r = tools.delete_group("Chess Club")
    assert r.pending.summary == "Delete the group Chess Club; its people and sub-groups move to the top level"
    r.pending.run()
    assert store.find_people("Sam")[0]["group_id"] is None


# --- plan_time (fitting hours around what's planned) ---

def test_plan_time_fits_around_blocks_and_spreads(tools, store):
    # Mon 26 Oct: lectures 09:00-12:00 and 14:00-16:00; Tue: lecture 10:00-11:00.
    tools.add_block("2026-10-26", "plan", "09:00", "12:00", "Lectures", category="Classes")
    tools.add_block("2026-10-26", "plan", "14:00", "16:00", "Lectures", category="Classes")
    tools.add_block("2026-10-27", "plan", "10:00", "11:00", "Lectures", category="Classes")
    r = tools.plan_time("Revision", 6, "2026-10-26", "2026-10-28", category="Study", earliest="09:00", latest="18:00")
    assert r.pending and "Plan 6 h of Revision" in r.pending.summary
    assert store.blocks_between("2026-10-26", "2026-10-28", "plan")[0]["label"] == "Lectures"   # nothing yet
    r.pending.run()
    rev = [(b["date"], b["start"], b["end"]) for b in store.blocks_between("2026-10-26", "2026-10-28", "plan")
           if b["label"] == "Revision"]
    hours = sum((store.slot_of(e) - store.slot_of(s)) / 2 for _, s, e in rev)
    assert hours == 6
    assert {d for d, _, _ in rev} == {"2026-10-26", "2026-10-27", "2026-10-28"}          # spread over the days
    lectures = [b for b in store.blocks_between("2026-10-26", "2026-10-28", "plan") if b["label"] == "Lectures"]
    assert len(lectures) == 3                                                            # nothing overwritten
    for d, s, e in rev:                                                                  # all inside 09:00-18:00
        assert "09:00" <= s and e <= "18:00"


def test_plan_time_when_it_does_not_all_fit(tools, store):
    for day in ("2026-10-26", "2026-10-27"):
        tools.add_block(day, "plan", "09:00", "17:00", "Work", category="Work")
    r = tools.plan_time("Reading", 5, "2026-10-26", "2026-10-27", earliest="09:00", latest="18:00",
                        category="Reading")
    assert "only 2 of 5 hours fit" in r.pending.summary
    assert "no free time" in tools.plan_time("Reading", 1, "2026-10-26", "2026-10-26", earliest="09:00",
                                             latest="17:00", category="Reading").text


def test_plan_time_weekdays_only_and_validation(tools, store):
    r = tools.plan_time("Gym", 2, "2026-10-31", "2026-11-02", category="Exercise", weekdays_only=True)
    assert "Mon 2 Nov" in r.pending.summary and "Sat" not in r.pending.summary
    with pytest.raises(tools.ToolError):
        tools.plan_time("Gym", 0, "2026-10-26", "2026-10-27")
    with pytest.raises(tools.ToolError):
        tools.plan_time("Gym", 2, "2026-10-26", "2026-10-27", earliest="18:00", latest="09:00")


def test_plan_time_spaces_sittings_out(tools, store):
    r = tools.plan_time("Revision", 6, "2026-11-02", "2026-11-06", category="Study")     # Mon-Fri, 3 sittings
    assert [x.split(" ")[0] for x in r.pending.summary.split(": ", 1)[1].split("; ")] == ["Mon", "Wed", "Fri"]


def test_log_interaction_tools(tools, store, friday):
    tools.add_person("Priya")
    assert tools.log_interaction("priya", "Coffee; talked about her thesis").text == \
        "Logged for Priya on Fri 30 Oct: Coffee; talked about her thesis"
    tools.log_interaction("Priya", "Lab meeting", date="2026-10-20")
    assert tools.person_history("Priya").text.splitlines()[1:] == [
        "- Fri 30 Oct (2026-10-30): Coffee; talked about her thesis", "- Tue 20 Oct (2026-10-20): Lab meeting"]
    assert "Last interaction Fri 30 Oct: Coffee" in tools.find_people().text
    with pytest.raises(tools.ToolError, match="no one called"):
        tools.log_interaction("Zed", "Lunch")


def test_log_water(tools, store):
    tools.log_day(DAY, mood=4)
    r = tools.log_day(DAY, water=1.5)
    assert r.changed and store.get_entry(DAY)["water"] == 1.5 and store.get_entry(DAY)["mood"] == 4
    r = tools.log_day(DAY, water=2)                      # a running total: updates straight away
    assert not r.pending and "water 1.5 → 2 L" in r.text and store.get_entry(DAY)["water"] == 2
    tools.log_day(DAY, sleep=7)                          # other fields leave water alone
    assert store.get_entry(DAY)["water"] == 2
    assert "water 2 L" in tools.query_entries(DAY, DAY).text
    with pytest.raises(tools.ToolError):
        tools.log_day(DAY, water=40)
