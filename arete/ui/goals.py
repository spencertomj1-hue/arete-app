"""
Goals: measurable targets for a month or a year ("Gym: 12 sessions"), each
with a progress bar. Progress is either updated by hand (− / + / a number) or
counted automatically from a habit's ticks in that period.

The Goals page lists this month's and this year's goals (with ‹ › to look at
other months and years); Home shows the current ones as a compact list of bars.
"""

from html import escape

import streamlit as st

from arete import store

PAGE = "Goals"                                  # sidebar label (also what "See all" switches to)
NAMES = {"monthly": "Monthly", "yearly": "Yearly"}
CURRENT = {"monthly": "This month", "yearly": "This year"}
BY_HAND, BY_HABIT = "Update it myself", "Count a habit's ticks"


def _num(x):
    return f"{store._num(x):g}" if isinstance(x, float) else str(x)


def amount(g):
    """"7 / 12 sessions"."""
    return f"{_num(g['value'])} / {_num(g['target'])}" + (f" {g['unit']}" if g["unit"] else "")


def fraction(g):
    return min(g["value"] / g["target"], 1.0) if g["target"] else 0.0


def _start_key(period):
    return f"goals_start_{period}"


def _shift(period, n):
    """‹ / › / back to the current month or year."""
    key = _start_key(period)
    if n == 0:
        st.session_state[key] = store.start_of_period(period, store.london_today())
    else:
        st.session_state[key] = store.shift_period(period, st.session_state[key], n)


def _go_to_goals():
    st.session_state["page"] = PAGE        # the sidebar radio's key; button callbacks run before it's drawn


# ---------------------------------------------------------------------------
# The form shared by "New goal" and "Edit"
# ---------------------------------------------------------------------------

def _goal_form(key, goal=None):
    """Title, target, unit and how progress is tracked. Returns the values, or None if the habit choice is missing."""
    habits = dict(store.get_habits())                       # id -> name
    title = st.text_input("Goal", value=goal["title"] if goal else "", key=f"{key}_title",
                          placeholder="e.g. Gym")
    a, b = st.columns(2)
    target = a.number_input("Target", min_value=0.5, step=1.0, value=float(goal["target"]) if goal else 10.0, format="%g",
                            key=f"{key}_target")
    unit = b.text_input("Unit (optional)", value=(goal["unit"] or "") if goal else "", key=f"{key}_unit",
                        placeholder="sessions, km, books…")
    linked = bool(goal and goal["habit_id"] is not None)
    how = st.radio("Progress", [BY_HAND, BY_HABIT], index=1 if linked else 0, key=f"{key}_how", horizontal=True,
                   help="Count a habit's ticks: every day you tick that habit in the period adds 1.")
    habit_id = None
    if how == BY_HABIT:
        if not habits:
            st.caption("You have no habits yet. Add one on the Daily Log page, or update this goal yourself.")
            return None
        ids = list(habits)
        habit_id = st.selectbox("Habit", ids, format_func=habits.get, key=f"{key}_habit",
                                index=ids.index(goal["habit_id"]) if linked and goal["habit_id"] in ids else 0)
    return {"title": title.strip(), "target": target, "unit": unit.strip() or None, "habit_id": habit_id}


def _new_goal(period, start):
    with st.popover("New goal", icon=":material/add:", width="stretch"):
        values = _goal_form(f"new_{period}")
        if st.button("Add goal", key=f"new_{period}_add", type="primary", disabled=not values or not values["title"]):
            store.add_goal(values["title"], period, start, values["target"], values["unit"], values["habit_id"])
            for k in [k for k in st.session_state if k.startswith(f"new_{period}_")]:
                del st.session_state[k]                      # an empty form next time
            st.rerun()


def _edit_goal(g):
    with st.popover("", icon=":material/more_horiz:", help="Edit or delete"):
        key = f"edit_{g['id']}"
        values = _goal_form(key, g)
        if not g["habit"]:
            progress = st.number_input("Progress so far", min_value=0.0, step=1.0, value=float(g["progress"]), format="%g",
                                       key=f"{key}_progress")
        save, delete = st.columns(2)
        if save.button("Save", key=f"{key}_save", type="primary", width="stretch",
                       disabled=not values or not values["title"]):
            store.update_goal(g["id"], **values)
            if not g["habit"] and values["habit_id"] is None:
                store.set_goal_progress(g["id"], progress)
            st.rerun()
        if delete.button("Delete", key=f"{key}_delete", icon=":material/delete:", width="stretch"):
            store.delete_goal(g["id"])
            st.rerun()


# ---------------------------------------------------------------------------
# Goals page
# ---------------------------------------------------------------------------

def _goal_row(g):
    with st.container(border=True):
        text, minus, plus, more = st.columns([5, 1, 1, 1.4], vertical_alignment="center")
        pct = round(100 * fraction(g))
        text.markdown(f"**{escape(g['title'])}**  \n:gray[{escape(amount(g))} · {pct}%]")
        if g["habit"]:
            minus.empty()
            plus.empty()
        else:
            minus.button("", icon=":material/remove:", key=f"minus_{g['id']}", help="Take 1 off",
                         on_click=store.add_goal_progress, args=(g["id"], -1), disabled=g["value"] <= 0)
            plus.button("", icon=":material/add:", key=f"plus_{g['id']}", help="Add 1",
                        on_click=store.add_goal_progress, args=(g["id"], 1))
        with more:
            _edit_goal(g)
        st.html(f'<div class="hg-bar">{_fill(g)}</div>' + BAR_CSS.replace("ACCENT", _accent()))
        if g["habit"]:
            st.caption(f":material/sync: Counted from the habit **{escape(g['habit'])}**")


def _section(period, today):
    start = st.session_state.setdefault(_start_key(period), store.start_of_period(period, today))
    current = start == store.start_of_period(period, today)
    goals = store.goals_in(period, start)
    done = sum(fraction(g) >= 1 for g in goals)
    with st.container(border=True):
        st.subheader(f"{NAMES[period]} :gray[{done}/{len(goals)}]", anchor=False)
        prev, label, nxt, back = st.columns([1, 4, 1, 3], vertical_alignment="center")
        prev.button("", icon=":material/chevron_left:", key=f"prev_{period}", on_click=_shift, args=(period, -1),
                    help=f"Previous {'month' if period == 'monthly' else 'year'}")
        label.markdown(f"**{store.period_title(period, start, today)}**")
        nxt.button("", icon=":material/chevron_right:", key=f"next_{period}", on_click=_shift, args=(period, 1),
                   help=f"Next {'month' if period == 'monthly' else 'year'}")
        if not current:
            back.button(CURRENT[period], key=f"now_{period}", on_click=_shift, args=(period, 0))
        for g in goals:
            _goal_row(g)
        if not goals:
            st.caption("No goals yet.")
        _new_goal(period, start)


def render_page():
    st.header("Goals")
    st.caption("Measurable targets for the month or year, and deadlines. Day-to-day to-dos live on Home.")
    today = store.london_today()
    for col, period in zip(st.columns(2, gap="medium"), store.GOAL_PERIODS):
        with col:
            _section(period, today)
    from arete.ui import deadlines
    deadlines.render_section()


# ---------------------------------------------------------------------------
# Home: this month's and this year's goals as progress bars
# ---------------------------------------------------------------------------

def _accent():
    return st.get_option("theme.primaryColor") or "#ff4b4b"


def _fill(g):
    return f'<i style="width:{100 * fraction(g):.1f}%"></i>'


# The progress bar used on both pages, in the app's accent colour (st.progress has its own colour).
BAR_CSS = """<style>
  .hg-bar { height: 7px; border-radius: 999px; background: rgba(128, 128, 128, 0.18); overflow: hidden; }
  .hg-bar i { display: block; height: 100%; border-radius: 999px; background: ACCENT; transition: width 0.4s ease; }
</style>"""


CHECK = ('<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" '
         'fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def render_home():
    today = store.london_today()
    accent = _accent()
    rows = []
    for period in store.GOAL_PERIODS:
        for g in store.goals_in(period, today):
            pct = 100 * fraction(g)
            when = "this month" if period == "monthly" else "this year"
            rows.append(
                f'<div class="hg-row{" done" if pct >= 100 else ""}">'
                f'<div class="hg-top"><span class="hg-title">{escape(g["title"])}</span>'
                f'<span class="hg-when">{when}</span>'
                f'<span class="hg-amount">{CHECK if pct >= 100 else ""}{escape(amount(g))}</span></div>'
                f'<div class="hg-bar">{_fill(g)}</div></div>'
            )
    head, link = st.columns([4, 1], vertical_alignment="center")
    head.markdown("**Goals**")
    link.button("See all", key="home_goals_all", type="tertiary", on_click=_go_to_goals)
    body = "".join(rows) or '<div class="hg-empty">No goals for this month or year yet.</div>'
    st.html(f"""<style>
      .hg {{ display: grid; gap: 9px; }}
      .hg-top {{ display: flex; align-items: baseline; gap: 8px; font-size: 0.86rem; margin-bottom: 3px; }}
      .hg-title {{ font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
      .hg-when {{ font-size: 0.72rem; opacity: 0.6; white-space: nowrap; }}
      .hg-amount {{ margin-left: auto; display: inline-flex; align-items: center; gap: 4px; opacity: 0.75;
                    font-variant-numeric: tabular-nums; white-space: nowrap; }}
      .hg-row.done .hg-amount {{ color: {accent}; opacity: 1; font-weight: 600; }}
      .hg-empty {{ opacity: 0.6; font-size: 0.86rem; }}
    </style><div class="hg">{body}</div>""" + BAR_CSS.replace("ACCENT", accent))
