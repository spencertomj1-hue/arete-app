"""
The weekly review on screen: a pop-up the first time Arete is opened after 7pm
on Sunday (maybe_show), and the history at the bottom of Stats (history).
The numbers come from arete/review.py.
"""

from datetime import timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from arete import regional
from arete import review
from arete import store


def _week_title(first):
    last = first + timedelta(days=6)
    return f"{regional.day_month(first, weekday=False)} – {regional.day_month(last, weekday=False, year=True)}"


def _delta(now, before, unit="", fmt="{:g}"):
    if now is None:
        return "–", None
    return fmt.format(now) + unit, (None if before is None else round(now - before, 1))


def render(first, key):
    """The review of the week starting `first`. `key` keeps widget keys apart (dialog vs Stats)."""
    s = review.summary(first)
    if not s["complete"]:
        st.caption("This week isn't over yet: these are the numbers so far.")

    # --- The headline numbers ---
    habits_hit = sum(h["hit"] for h in s["habits"])
    t = s["todos"]
    a, b, c, d = st.columns(4)
    a.metric("Plan followed", f"{s['followed_pct']}%" if s["followed_pct"] is not None else "–",
             help="The share of your planned time where you did what you planned (same category).")
    b.metric("Hours logged", f"{s['actual_hours']:g} h", help=f"{s['planned_hours']:g} h were planned.")
    c.metric("Habits on target", f"{habits_hit}/{len(s['habits'])}" if s["habits"] else "–")
    d.metric("To-dos done", f"{t['weekly_done'] + t['daily_done']}/{t['weekly'] + t['daily']}"
             if t["weekly"] + t["daily"] else "–")
    log = s["log"]
    a, b, c, d = st.columns(4)
    for col, name, k, unit, fmt in ((a, "Mood", "mood", "/5", "{:g}"), (b, "Sleep", "sleep", " h", "{:g}"),
                                    (c, "Energy", "energy", "/5", "{:g}"), (d, "Water", "water", "", "{}")):
        now, before = log[k]["now"], log[k]["before"]
        if k == "water":
            value = regional.water_text(now) if now is not None else "–"
            delta = None if now is None or before is None else \
                round(regional.water_out(now) - regional.water_out(before), 1)
        else:
            value, delta = _delta(now, before, unit, fmt)
        col.metric(f"Avg {name.lower()}", value, delta=delta, help="Change from the week before.")
    st.caption(f"Daily log filled in on {s['days_logged']} of 7 days.")

    # --- Time: planned vs done per category ---
    if s["time"]:
        rows = [{"Category": x["category"], "Hours": x[track], "Track": label}
                for x in s["time"] for track, label in (("plan", "Planned"), ("actual", "Done"))]
        fig = px.bar(pd.DataFrame(rows), x="Category", y="Hours", color="Track", barmode="group",
                     title="Time: planned vs done", text_auto=".1f",
                     color_discrete_map={"Planned": "#9AA0AE", "Done": regional_accent()})
        fig.update_xaxes(title=None)
        fig.update_layout(legend_title_text="")
        st.plotly_chart(fig, width="stretch", key=f"{key}_time")

    left, right = st.columns(2)
    with left:
        if s["habits"]:
            st.markdown("**Habits**")
            for h in s["habits"]:
                mark = ":material/check_circle:" if h["hit"] else ":material/radio_button_unchecked:"
                st.markdown(f"{mark} {h['name']} · {h['ticks']}/{h['target']}")
        dl = s["deadlines"]
        if dl["met"] or dl["missed"] or dl["upcoming"]:
            st.markdown("**Deadlines**")
            for x in dl["met"]:
                st.markdown(f":material/check_circle: {x['title']} (done)")
            for x in dl["missed"]:
                st.markdown(f":red[:material/cancel: {x['title']} (not ticked off)]")
            for x in dl["upcoming"]:
                st.markdown(f":material/schedule: Next week: {x['title']}, {regional.day_month(x['due'])}")
    with right:
        if s["people"]:
            st.markdown("**People you saw**")
            for p in s["people"]:
                st.markdown(f"{p['person']} · {p['note']}")
        if s["journal"]:
            st.markdown(f"**Journal** ({len(s['journal'])} entr{'y' if len(s['journal']) == 1 else 'ies'})")
            for e in reversed(s["journal"]):
                line = e["text"].strip().splitlines()[0]
                st.markdown(f"{regional.day_month(e['date'])} · {line[:90]}{'…' if len(line) > 90 else ''}")

    # --- Reflections ---
    st.markdown("**Looking back**")
    saved = s["review"]
    with st.form(f"{key}_reflect", border=False):
        went = st.text_area("What went well?", saved.get("went_well") or "", height=90)
        change = st.text_area("What will you do differently next week?", saved.get("to_change") or "", height=90)
        if st.form_submit_button("Save", type="primary", icon=":material/save:"):
            store.save_review(first, went_well=went, to_change=change)
            st.toast("Saved", icon=":material/check_circle:")


def regional_accent():
    from arete.ui import appearance
    return appearance.accent()


@st.dialog("Your week", width="large")
def _dialog(first):
    st.subheader(_week_title(first), anchor=False)
    render(first, key="review_popup")


def maybe_show():
    """Once per session: the week's review, if it's due (after 7pm Sunday) and not seen yet."""
    if st.session_state.get("review_checked"):
        return
    st.session_state["review_checked"] = True
    week = review.should_show()
    if week:
        store.save_review(week, seen=True)            # shown once, even if it's closed straight away
        _dialog(week)


def history():
    """Stats page: pick a week to look at its review again (or this week so far)."""
    st.subheader("Weekly reviews", anchor=False)
    this_week = store.week_start_of(store.local_today())
    weeks = [store._as_date(r["week_start"]) for r in store.reviews()]
    options = [this_week] + [w for w in weeks if w != this_week]
    if len(options) == 1:
        st.caption("Your first review appears on Sunday evening. Meanwhile, here's this week so far.")
    pick = st.selectbox("Week", options, key="review_week",
                        format_func=lambda w: ("This week so far: " if w == this_week else "") + _week_title(w))
    with st.container(border=True):
        render(pick, key=f"review_{pick.isoformat()}")
