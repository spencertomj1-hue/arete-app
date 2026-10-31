"""Network: store rules and the Network page (AppTest), with the throwaway data folder."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_people_links_and_groups(store):
    assert [g["name"] for g in store.net_groups()] == ["Family", "Friends", "Uni", "Work", "Mentors", "Other"]
    sam = store.add_person("Sam", "Uni friend", "Google", store.find_net_group("FRIENDS"))
    alex = store.add_person("Alex")
    store.link_people(alex["id"], sam["id"], "Football")
    store.link_people(sam["id"], alex["id"], "Five-a-side")          # same pair, either order: one row
    assert store.net_links() == [{"a": min(sam["id"], alex["id"]), "b": max(sam["id"], alex["id"]), "reason": "Five-a-side"}]
    assert [(o["name"], r) for o, r in store.connections_of(sam["id"])] == [("Alex", "Five-a-side")]
    with pytest.raises(ValueError):
        store.link_people(sam["id"], sam["id"])
    store.update_person(sam["id"], works_for="", group_id=None)       # clear both
    assert (store.get_person(sam["id"])["works_for"], store.get_person(sam["id"])["group_id"]) == (None, None)
    store.update_person(sam["id"], description="Best man")            # the group stays as it is
    assert store.get_person(sam["id"])["description"] == "Best man"
    store.delete_person(alex["id"])
    assert store.net_links() == []


def test_sub_groups(store):
    northfield = store.add_net_group("Northfield", "#123abc")
    society = store.add_net_group("Physics Society", parent_id=northfield)                     # takes Northfield's colour
    lab = store.add_net_group("Robotics Lab", parent_id=northfield)
    names = [g["name"] for g in store.net_groups()]
    assert names[names.index("Northfield"):names.index("Northfield") + 3] == ["Northfield", "Physics Society", "Robotics Lab"]
    assert next(g for g in store.net_groups() if g["id"] == society)["colour"] == "#123ABC"
    assert store.group_path(society) == "Northfield › Physics Society"
    with pytest.raises(ValueError):
        store.update_net_group(northfield, parent_id=society)                       # no loops
    store.update_net_group(lab, parent_id=society)
    assert store.group_path(lab) == "Northfield › Physics Society › Robotics Lab"
    p = store.add_person("Sam", group_id=society)
    store.delete_net_group(society)                                              # people and sub-groups move up
    assert store.get_person(p["id"])["group_id"] == northfield
    assert store.group_path(lab) == "Northfield › Robotics Lab"


def test_find_people(store):
    store.add_person("Sam Smith")
    store.add_person("Sam")
    assert [p["name"] for p in store.find_people("sam")] == ["Sam"]           # exact wins
    assert [p["name"] for p in store.find_people("smi")] == ["Sam Smith"]


def test_page_and_edit_dialogs(store):
    northfield = store.add_net_group("Northfield")
    sam = store.add_person("Sam", group_id=store.add_net_group("Physics Society", parent_id=northfield))
    alex = store.add_person("Alex")
    store.link_people(sam["id"], alex["id"], "Football")
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Network").run()
    assert not at.exception and at.header[0].value == "Network"

    # What a card's buttons do; each opens its dialog. (AppTest reruns the whole page on a click
    # inside a dialog, unlike the browser, so the clicking is checked in a real browser instead.)
    at.session_state["net_edit"] = {"kind": "person", "id": sam["id"]}
    at.run()
    assert not at.exception
    assert at.text_input(key=f"net_edit_{sam['id']}_name").value == "Sam"
    assert at.button(key=f"net_unlink_{alex['id']}")
    at.session_state["net_edit"] = {"kind": "group", "id": northfield}
    at.run()
    assert at.text_input(key=f"net_editg_{northfield}_name").value == "Northfield"
    for edit in ({"kind": "new_person", "group": northfield}, {"kind": "new_group", "parent": northfield}):
        at.session_state["net_edit"] = edit
        at.run()
        assert not at.exception
    at.run()                                             # and they don't reopen on the next run
    assert not [t for t in at.text_input if t.key and t.key.startswith("net_")]


def test_interaction_log(store):
    sam = store.add_person("Sam")
    a = store.add_interaction(sam["id"], "Coffee; talked about her thesis", "2026-10-28")
    store.add_interaction(sam["id"], "Football", "2026-10-30")
    assert [i["note"] for i in store.interactions_of(sam["id"])] == ["Football", "Coffee; talked about her thesis"]
    assert store.last_interactions() == {sam["id"]: "2026-10-30"}
    store.delete_interaction(a)
    assert len(store.interactions_of(sam["id"])) == 1
    with pytest.raises(ValueError):
        store.add_interaction(sam["id"], "  ", "2026-10-30")
    store.delete_person(sam["id"])                                   # goes with the person
    assert store.last_interactions() == {}


def test_person_dialog_shows_interactions(store):
    sam = store.add_person("Sam")
    store.add_interaction(sam["id"], "Coffee in town", "2026-10-28")
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Network").run()
    at.session_state["net_edit"] = {"kind": "person", "id": sam["id"]}
    at.run()
    assert not at.exception
    assert any("Coffee in town" in m.value for m in at.markdown)
