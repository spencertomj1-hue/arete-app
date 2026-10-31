"""
Buttons above the timetable, so everything works without the Claude assistant:

  Add block    a form for one block (also the keyboard way to add one)
  Fit time     smart scheduling: fit N hours of something into the Plan's free gaps
  Categories   add / rename / recolour categories, activity defaults, Outlook's colour

They call the same functions as Claude's tools (tools.add_block, plan_time,
edit_category, ...), so the rules and the Confirm previews are identical.
"""

from datetime import timedelta

import re

import streamlit as st

from arete import regional
from arete import store
from arete import tools

AUTO = "Automatic (from the activity)"
PENDING = "tt_tool_pending"          # a tools.Pending preview waiting for Confirm


def _times(first, last):
    return [store.slot_time(i) for i in range(first, last + 1)]


def _at(options, wanted, fallback=0):
    """Index of a default time in a list of times (or the fallback if the day doesn't include it)."""
    return options.index(wanted) if wanted in options else min(fallback, len(options) - 1)


def _category_names():
    return list(store.load_categories()["name"])


def _for_people(message):
    """Tool errors are worded for Claude ("Ask the user ..."); drop those bits for a person."""
    message = re.sub(r"\s*(Ask the user|Use list_\w+|Set create=true)[^.]*(: [^.]*)?\.", "", message)
    return message.replace("There's no default category for", "Pick a category: there's no default for")


def _category_choice(label, category):
    """None for Automatic, else the picked name; shows an error (and returns False) if Automatic can't work."""
    if category != AUTO:
        return category
    if label.strip() and store.resolve_category(label=label.strip()) is None:
        st.error(f"Pick a category for “{label.strip()}” (it has no default yet; you can set one in Categories).",
                 icon=":material/error:")
        return False
    return None


def _call(fn, *args, **kwargs):
    """Run a tool: a preview waits for Confirm; otherwise say what happened and refresh the page."""
    try:
        r = fn(*args, **kwargs)
    except tools.ToolError as e:
        st.error(_for_people(str(e)), icon=":material/error:")
        return
    if r.pending:
        st.session_state[PENDING] = r.pending
        st.rerun(scope="fragment")
    st.session_state.pop(PENDING, None)
    st.toast(r.text, icon=":material/check_circle:")
    st.rerun()


def _confirm_box(yes="Confirm"):
    """Show a waiting preview with Confirm / Cancel; True while one is waiting."""
    pending = st.session_state.get(PENDING)
    if not pending:
        return False
    st.warning(pending.summary, icon=":material/preview:")
    a, b, _ = st.columns([1, 1, 3])
    if a.button(yes, type="primary", key="tt_tool_yes"):
        r = pending.run()
        st.session_state.pop(PENDING, None)
        st.toast(r.text, icon=":material/check_circle:")
        st.rerun()
    if b.button("Cancel", key="tt_tool_no"):
        st.session_state.pop(PENDING, None)
        st.rerun(scope="fragment")
    return True


# ---------------------------------------------------------------------------
# Add block
# ---------------------------------------------------------------------------

@st.dialog("Add a block", width="large")
def _add_block_dialog(day):
    if _confirm_box("Overwrite"):
        return
    with st.form("tt_add_form", border=False):
        a, b = st.columns(2)
        when = a.date_input("Day", value=day, format=regional.input_format())
        track = b.segmented_control("Column", ["Plan", "Actual"], default="Plan",
                                    help="Plan: what you intend to do. Actual: what you really did.")
        a, b = st.columns(2)
        starts, ends = _times(0, store.SLOTS - 1), _times(1, store.SLOTS)
        start = a.selectbox("From", starts, index=_at(starts, "09:00"), format_func=regional.time)
        end = b.selectbox("To", ends, index=_at(ends, "10:00", 1), format_func=regional.time, help="Times after midnight count as the same day.")
        label = st.text_input("What", placeholder="e.g. Lecture, Gym, Revision")
        category = st.selectbox("Category", [AUTO] + _category_names())
        details = st.text_area("Details (optional)", height=80)
        if st.form_submit_button("Add", type="primary", icon=":material/add:"):
            cat = _category_choice(label, category)
            if cat is not False:
                _call(tools.add_block, when.isoformat(), (track or "Plan").lower(), start, end, label,
                      category=cat, details=details.strip() or None)
    st.caption("If it overlaps blocks already there, you'll see what changes before anything is overwritten.")


# ---------------------------------------------------------------------------
# Fit time (smart scheduling)
# ---------------------------------------------------------------------------

@st.dialog("Fit time into your Plan", width="large")
def _fit_dialog(day, label="", last=None, category=None):
    st.caption("Finds free gaps in your Plan (around your blocks and Outlook events) and spreads the "
               "hours over the days, with a break between sittings. You'll see the times before anything is added.")
    if _confirm_box("Add to Plan"):
        return
    today = store.local_today()
    first = max(day, today)
    last = max(first, last) if last else first + timedelta(days=6)
    with st.form("tt_fit_form", border=False):
        a, b = st.columns([2, 1])
        label = a.text_input("What", value=label, placeholder="e.g. Revision")
        hours = b.number_input("Hours in total", 0.5, 60.0, 6.0, step=0.5)
        options = [AUTO] + _category_names()
        category = st.selectbox("Category", options, key="tt_fit_cat",
                                index=options.index(category) if category in options else 0)
        a, b = st.columns(2)
        span = a.date_input("Between", value=(first, last), format=regional.input_format(),
                            help="Pick the first and last day.")
        longest = b.number_input("Longest sitting (hours)", 0.5, 8.0, 2.0, step=0.5)
        a, b, c = st.columns([1, 1, 1], vertical_alignment="bottom")
        starts, ends = _times(0, store.SLOTS - 1), _times(1, store.SLOTS)
        earliest = a.selectbox("Not before", starts, index=_at(starts, "09:00"), format_func=regional.time)
        latest = b.selectbox("Not after", ends, index=_at(ends, "21:00", len(ends) - 1), format_func=regional.time)
        weekdays = c.checkbox("Weekdays only")
        if st.form_submit_button("Find times", type="primary", icon=":material/event_available:"):
            if not label.strip():
                st.error("Say what it's for, e.g. Revision.")
            elif len(span) != 2:
                st.error("Pick both the first and the last day.")
            elif (cat := _category_choice(label, category)) is not False:
                _call(tools.plan_time, label, hours, span[0].isoformat(), span[1].isoformat(),
                      category=cat, earliest=earliest, latest=latest,
                      max_block_hours=longest, weekdays_only=weekdays)


# ---------------------------------------------------------------------------
# Categories, activity defaults and Outlook's colour
# ---------------------------------------------------------------------------

@st.dialog("Categories", width="large")
def _categories_dialog():
    cats = store.load_categories().to_dict("records")
    groups = store.groups()

    st.markdown("**Your categories**")
    with st.form("tt_cats_form", border=False):
        edits = []
        for c in cats:
            a, b, g = st.columns([4, 1, 2], vertical_alignment="bottom")
            name = a.text_input("Name", c["name"], key=f"tt_cat_name_{c['id']}", label_visibility="collapsed")
            colour = b.color_picker("Colour", c["colour"], key=f"tt_cat_col_{c['id']}", label_visibility="collapsed")
            grp = g.selectbox("Group", groups, index=groups.index(c["grp"]), key=f"tt_cat_grp_{c['id']}",
                              label_visibility="collapsed")
            edits.append((c, name.strip(), colour, grp))
        if st.form_submit_button("Save changes", type="primary", icon=":material/save:"):
            names = [n.lower() for _, n, _, _ in edits]
            if any(not n for n in names):
                st.error("Every category needs a name.")
            elif len(set(names)) < len(names):
                st.error("Two categories can't have the same name.")
            else:
                for c, name, colour, grp in edits:
                    if (name, colour.upper(), grp) != (c["name"], c["colour"].upper(), c["grp"]):
                        store.save_category(c["id"], name, colour, grp, c["text_colour"])
                st.toast("Categories saved", icon=":material/check_circle:")
                st.rerun()

    st.markdown("**New category**")
    with st.form("tt_cat_new", clear_on_submit=True, border=False):
        a, b, g, s = st.columns([4, 1, 2, 2], vertical_alignment="bottom")
        name = a.text_input("Name", placeholder="e.g. Music")
        colour = b.color_picker("Colour", "#3E8EF7")
        grp = g.selectbox("Group", groups)
        if s.form_submit_button("Add", icon=":material/add:"):
            if not name.strip():
                st.error("Give it a name.")
            else:
                _call(tools.edit_category, name, colour=colour, group=grp, create=True)

    st.divider()
    st.markdown("**Activity defaults**")
    st.caption("Typing one of these in the timetable picks its category automatically.")
    names = dict(zip(store.load_categories()["id"], store.load_categories()["name"]))
    for row in store.load_label_map().to_dict("records"):
        a, b, c = st.columns([3, 3, 1], vertical_alignment="center")
        a.write(row["label"])
        b.write(names.get(row["category_id"], "—"))
        if c.button("", icon=":material/delete:", key=f"tt_lab_del_{row['label']}", help="Remove"):
            store.remove_label_default(row["label"])
            st.rerun(scope="fragment")
    with st.form("tt_lab_new", clear_on_submit=True, border=False):
        a, b, c = st.columns([3, 3, 1], vertical_alignment="bottom")
        label = a.text_input("Activity", placeholder="e.g. Piano")
        cat = b.selectbox("Category", list(names.values()))
        if c.form_submit_button("Add", icon=":material/add:"):
            _call(tools.set_activity_default, label, cat)

    st.divider()
    st.markdown("**Outlook events**")
    current = store.outlook_category("")
    ids = list(names)
    pick = st.selectbox("Colour Outlook events as", ids, index=ids.index(current) if current in ids else 0,
                        format_func=names.get, key="tt_outlook_cat")
    if pick != current:
        store.save_settings(outlook_category=pick)
        st.toast(f"Outlook events now use {names[pick]}", icon=":material/check_circle:")


# ---------------------------------------------------------------------------
# The toolbar
# ---------------------------------------------------------------------------

def open_fit(label, last):
    """Fit time for something specific (e.g. a deadline), finishing by `last`."""
    st.session_state.pop(PENDING, None)
    names = _category_names()
    _fit_dialog(store.local_today(), label, last, "Study" if "Study" in names else None)


def toolbar(day):
    row = st.container(horizontal=True, gap="small")     # sized to the text, so bigger text never truncates
    a = b = c = row
    if a.button("Add block", icon=":material/add:", key="tt_btn_add"):
        st.session_state.pop(PENDING, None)
        _add_block_dialog(day)
    if b.button("Fit time", icon=":material/event_available:", key="tt_btn_fit",
                help="Fit hours of something (e.g. 6 h of revision) into your free time"):
        st.session_state.pop(PENDING, None)
        _fit_dialog(day)
    if c.button("Categories", icon=":material/palette:", key="tt_btn_cats"):
        _categories_dialog()
