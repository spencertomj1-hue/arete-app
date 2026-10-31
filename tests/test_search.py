"""Search everything: what it finds, and Open going to the right place (Home's search box)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def seed(store):
    from arete import tools
    import importlib
    tools = importlib.reload(tools)
    store.add_todo("Email Priya about the thesis", "weekly", "2026-10-28")
    p = store.add_person("Priya", "Lab partner", "University")
    store.add_interaction(p["id"], "Coffee, talked about her thesis", "2026-10-21")
    store.save_journal("2026-10-22", "Long day. Priya fixed the 50% bug")
    store.add_deadline("Thesis draft", "2026-11-20")
    tools.add_block("2026-10-29", "plan", "14:00", "16:00", "Thesis writing", category="Study")
    store.add_habit("Read")
    return p


def test_search_everything(store):
    seed(store)
    kinds = lambda q: sorted(r["kind"] for r in store.search(q))
    assert kinds("priya") == ["Journal", "Met", "Person", "To-do"]
    assert kinds("THESIS") == ["Deadline", "Met", "Timetable", "To-do"]
    assert kinds("thesis priya") == ["Met", "To-do"]                  # every word must match
    assert kinds("50%") == ["Journal"] and store.search("5_%") == []  # wildcards are just characters
    assert store.search("  ") == [] and kinds("read") == ["Habit"]


def test_search_box_and_open(store):
    p = seed(store)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input(key="global_search").set_value("priya").run()
    assert not at.exception
    assert any("Lab partner" in h.value for h in at.get("html"))
    person = next(b for b in at.button if b.key and b.key.startswith("search_open_") and "Person" in b.key)
    person.click().run()
    assert not at.exception and at.sidebar.radio[0].value == "Network"
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input(key="global_search").set_value("thesis writing").run()
    next(b for b in at.button if b.key and "Timetable" in b.key).click().run()
    assert at.sidebar.radio[0].value == "Timetable" and str(at.session_state["tt_day"]) == "2026-10-29"
