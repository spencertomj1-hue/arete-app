"""
Network page: a web of the people the user knows (network_component.js/.css),
as a tree: You -> groups -> sub-groups -> people, with lines across it between
people who know each other.

Most people get added by telling Claude about them. Here, "Add someone" /
"Add group" add things by hand, and the Edit buttons on the cards in the web
open a dialog to change or remove a person or a group.
"""

from pathlib import Path

import streamlit as st

from arete import regional
from arete import store

HERE = Path(__file__).parent
PAGE = "Network"

_network_component = st.components.v2.component(
    "arete_network",
    html='<div class="nw-root"></div>',
    css=(HERE / "components" / "network_component.css").read_text(encoding="utf-8"),
    js=(HERE / "components" / "network_component.js").read_text(encoding="utf-8"),
)


def _on_edit():
    """A card's Edit / Add button: remember which dialog to open on this run."""
    st.session_state["net_edit"] = st.session_state["network"]["edit"]


def _group_picker(label, key, current=None, exclude=None, none_label="No group"):
    """Selectbox of groups shown as "Northfield › Physics Society"; returns a group id or None."""
    groups = store.net_groups()
    if exclude:
        groups = [g for g in groups if g["id"] not in exclude]
    options = [None] + [g["id"] for g in groups]
    names = {g["id"]: store.group_path(g["id"], store.net_groups()) for g in groups}
    return st.selectbox(label, options, index=options.index(current) if current in options else 0,
                        format_func=lambda gid: names.get(gid, none_label), key=key)


def _below(group_id):
    """A group and all its sub-groups (it can't be moved inside any of them)."""
    groups = store.net_groups()
    out, todo = {group_id}, [group_id]
    while todo:
        kids = [g["id"] for g in groups if g["parent_id"] in todo]
        todo = [k for k in kids if k not in out]
        out.update(todo)
    return out


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def _person_form(key, p=None, group=None):
    """Name, works for, group, description. Returns them, or None until a name is given."""
    a, b = st.columns(2)
    name = a.text_input("Name", p["name"] if p else "", key=f"{key}_name")
    works_for = b.text_input("Works for", (p["works_for"] or "") if p else "", key=f"{key}_works",
                             placeholder="Company, university, team…")
    gid = _group_picker("Group", f"{key}_group", p["group_id"] if p else group)
    description = st.text_area("Description", (p["description"] or "") if p else "", key=f"{key}_desc",
                               placeholder="Who they are, how you know them…", height=80)
    return (name, description, works_for, gid) if name.strip() else None


@st.dialog("Add someone")
def _new_person_dialog(group=None):
    values = _person_form("net_new", group=group)
    if st.button("Add", type="primary"):
        if not values:
            st.error("Give them a name.")
        else:
            store.add_person(*values)
            st.rerun()


@st.dialog("Edit person", width="medium")
def _person_dialog(person_id):
    p = store.get_person(person_id)
    if not p:
        st.caption("This person has been removed.")
        return
    values = _person_form(f"net_edit_{person_id}", p)
    if st.button("Save", type="primary"):
        if not values:
            st.error("They need a name.")
        else:
            store.update_person(person_id, *values)
            st.rerun()

    st.markdown("**Connections**")
    links = store.connections_of(person_id)
    if not links:
        st.caption("None yet.")
    for other, reason in links:
        left, right = st.columns([6, 1], vertical_alignment="center")
        left.markdown(other["name"] + (f" · :gray[{reason}]" if reason else ""))
        if right.button("", icon=":material/link_off:", key=f"net_unlink_{other['id']}", help="Remove this connection"):
            store.unlink_people(person_id, other["id"])
            st.rerun(scope="fragment")
    others = [o for o in store.net_people() if o["id"] != person_id and o["id"] not in {x["id"] for x, _ in links}]
    if others:
        a, b, c = st.columns([3, 4, 2], vertical_alignment="bottom")
        names = {o["id"]: o["name"] for o in others}
        other = a.selectbox("Connect to", list(names), format_func=names.get, key=f"net_link_to_{person_id}")
        reason = b.text_input("How they know each other", key=f"net_link_why_{person_id}", placeholder="e.g. Work together")
        if c.button("Connect", icon=":material/link:", key=f"net_link_{person_id}"):
            store.link_people(person_id, other, reason)
            st.rerun(scope="fragment")

    st.markdown("**Times you've met or spoken**")
    with st.form(f"net_meet_{person_id}", clear_on_submit=True, border=False):
        a, b, c = st.columns([2, 5, 2], vertical_alignment="bottom")
        when = a.date_input("When", value=store.local_today(), format=regional.input_format())
        note = b.text_input("What happened", placeholder="e.g. Coffee; talked about her thesis")
        if c.form_submit_button("Add", icon=":material/add:") and note.strip():
            store.add_interaction(person_id, note, when)
            st.rerun(scope="fragment")
    history = store.interactions_of(person_id)
    if not history:
        st.caption("Nothing logged yet." + (" You can also tell Arete, e.g. \"had coffee with them today\"."
                                            if store.ai_enabled() else ""))
    for item in history:
        left, right = st.columns([6, 1], vertical_alignment="center")
        d = store._as_date(item["date"])
        left.markdown(f"**{regional.day_month(d, year=True)}** · {item['note']}")
        if right.button("", icon=":material/delete:", key=f"net_meet_del_{item['id']}", help="Remove this"):
            store.delete_interaction(item["id"])
            st.rerun(scope="fragment")

    st.divider()
    if st.toggle(f"Remove {p['name']} from the network", key=f"net_del_{person_id}"):
        if st.button("Remove", type="primary", icon=":material/delete:", key=f"net_del_go_{person_id}"):
            store.delete_person(person_id)
            st.rerun()


def _group_form(key, g=None, parent=None):
    a, b = st.columns([3, 1])
    name = a.text_input("Group name", g["name"] if g else "", key=f"{key}_name", placeholder="e.g. Northfield, Physics Society")
    start = g["colour"] if g else next((x["colour"] for x in store.net_groups() if x["id"] == parent), "#8B8D98")
    colour = b.color_picker("Colour", start, key=f"{key}_colour")
    inside = _group_picker("Inside", f"{key}_parent", g["parent_id"] if g else parent,
                           exclude=_below(g["id"]) if g else None, none_label="Nothing (a top-level group)")
    return name.strip(), colour, inside


@st.dialog("Add group")
def _new_group_dialog(parent=None):
    name, colour, inside = _group_form("net_newg", parent=parent)
    if st.button("Add", type="primary"):
        if not name:
            st.error("Give the group a name.")
        elif store.find_net_group(name):
            st.error(f"There's already a group called {name}.")
        else:
            store.add_net_group(name, colour, inside)
            st.rerun()


@st.dialog("Edit group")
def _group_dialog(group_id):
    g = next((x for x in store.net_groups() if x["id"] == group_id), None)
    if not g:
        st.caption("This group has been removed.")
        return
    name, colour, inside = _group_form(f"net_editg_{group_id}", g)
    if st.button("Save", type="primary"):
        clash = store.find_net_group(name)
        if not name:
            st.error("The group needs a name.")
        elif clash and clash != group_id:
            st.error(f"There's already a group called {name}.")
        else:
            store.update_net_group(group_id, name, colour, inside)
            st.rerun()
    st.divider()
    up = store.group_path(g["parent_id"]) if g["parent_id"] else "no group"
    if st.toggle(f"Delete {g['name']}", key=f"net_delg_{group_id}",
                 help=f"Its people and sub-groups move to {up}."):
        if st.button("Delete", type="primary", icon=":material/delete:", key=f"net_delg_go_{group_id}"):
            store.delete_net_group(group_id)
            st.rerun()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render_page():
    head, add_person, add_group = st.columns([4, 1, 1], vertical_alignment="bottom")
    head.header("Network", anchor=False)
    if add_person.button("Add someone", icon=":material/person_add:", width="stretch"):
        _new_person_dialog()
    if add_group.button("Add group", icon=":material/workspaces:", width="stretch"):
        _new_group_dialog()
    st.caption("Add people and groups with the buttons above"
               + (", or tell Arete (\"add Physics Society inside Northfield\", \"Sam is in Physics Society and knows Alex\")"
                  if store.ai_enabled() else "")
               + ". Click a circle for its details and an Edit button; drag to move around, scroll to zoom.")

    data = {
        "people": [{"id": p["id"], "name": p["name"], "description": p["description"],
                    "worksFor": p["works_for"], "groupId": p["group_id"],
                    "recent": store.interactions_of(p["id"], limit=3)} for p in store.net_people()],
        "today": store.local_today().isoformat(),
        "ai": store.ai_enabled(),
        "links": store.net_links(),
        "groups": store.net_groups(),
    }
    _network_component(data=data, key="network", on_edit_change=_on_edit)

    # A card button was pressed: open its dialog (once; the dialog's own reruns keep it open).
    edit = st.session_state.pop("net_edit", None)
    if edit:
        kind = edit.get("kind")
        if kind == "person":
            _person_dialog(edit["id"])
        elif kind == "group":
            _group_dialog(edit["id"])
        elif kind == "new_person":
            _new_person_dialog(edit.get("group"))
        elif kind == "new_group":
            _new_group_dialog(edit.get("parent"))
