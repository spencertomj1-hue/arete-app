"""
Arete — a small local app for logging mood, sleep, energy and habits.

Run with:   streamlit run app.py
All data is stored in life.db (SQLite) next to this file.
"""

from datetime import date, datetime
from html import escape
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import chat
import daily_log
import goals
import todos
import settings_ui
import store
import timetable
from store import entries_between, get_stats, init_db


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def _agenda():
    """Today's plan (own blocks and Outlook events) as a short list, the current one highlighted."""
    today = date.today().isoformat()
    blocks = [{**b, "outlook": False} for b in store.blocks_between(today, today, "plan")]
    all_day = []
    if st.session_state.get("tt_show_outlook", True):
        for ev in store.outlook_between(today, today):
            if ev["all_day"]:
                all_day.append(ev["title"])
            else:
                blocks.append({"start": ev["start"], "end": ev["end"], "label": ev["title"], "outlook": True,
                               "category_id": store.outlook_category(ev["title"]), "exact": ev["exact"]})
    blocks.sort(key=lambda b: store.slot_of(b["start"]))
    colours = dict(zip(*[store.load_categories()[c] for c in ("id", "colour")]))
    now = datetime.now()
    now_slot = store.slot_of(f"{now:%H}:{now.minute // 30 * 30:02d}")

    rows = []
    for b in blocks:
        current = store.slot_of(b["start"]) <= now_slot < store.slot_of(b["end"])
        rows.append(
            f'<div class="ag-row{" now" if current else ""}">'
            f'<span class="ag-dot" style="background:{colours.get(b["category_id"], "#999")}"></span>'
            f'<span class="ag-time">{b["exact"] if b["outlook"] else b["start"] + "–" + b["end"]}</span>'
            f'<span class="ag-label">{escape(b["label"])}'
            f'{" <span class=ag-ol title=Outlook>Outlook</span>" if b["outlook"] else ""}</span>'
            f'{"<span class=ag-now>Now</span>" if current else ""}</div>'
        )
    chips = "".join(f'<span class="ag-chip">{escape(t)}</span>' for t in all_day)
    body = (f'<div class="ag-chips">{chips}</div>' if chips else "") + (
        "".join(rows) or '<div class="ag-empty">Nothing planned for today yet.</div>')
    st.html(f"""<style>
      .ag {{ margin-bottom: 10px; max-height: 16vh; overflow-y: auto; }}   /* long days scroll inside */
      .ag h3 {{ margin: 0 0 4px; font-size: 1.05rem; font-weight: 600; }}
      .ag-row {{ display: flex; align-items: center; gap: 10px; padding: 2px 8px; border-radius: 7px; font-size: 0.86rem; }}
      .ag-row.now {{ background: rgba(128, 128, 128, 0.12); font-weight: 600; }}
      .ag-dot {{ width: 9px; height: 9px; border-radius: 3px; flex: none; }}
      .ag-time {{ width: 6.5em; flex: none; opacity: 0.7; font-variant-numeric: tabular-nums; }}
      .ag-label {{ min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
      .ag-now {{ margin-left: auto; font-size: 0.72rem; font-weight: 600; color: #ff4b4b; }}
      .ag-empty {{ opacity: 0.6; font-size: 0.86rem; }}
      .ag-ol {{ margin-left: 6px; padding: 0 6px; border: 1px solid rgba(128,128,128,.45); border-radius: 999px;
                font-size: 0.64rem; font-weight: 500; opacity: 0.75; }}
      .ag-chips {{ display: flex; flex-wrap: wrap; gap: 4px; margin: 0 0 4px; }}
      .ag-chip {{ padding: 1px 8px; border: 1px dashed rgba(128,128,128,.6); border-radius: 999px; font-size: 0.75rem; }}
    </style><div class="ag"><h3>Agenda</h3>{body}</div>""")


def page_home():
    """Landing page: agenda + today's Plan / Actual on the left; the Ask box, to-dos, habits and goals on the right."""
    left, right = st.columns([2, 3], gap="large")
    with left:
        _agenda()
        timetable.render_today()
    with right:
        chat.render(inline=True)
        todos.render_home()
        daily_log.render_home_habits()
        goals.render_home()


DAY_MS = 24 * 60 * 60 * 1000   # Plotly measures date steps in milliseconds


def _date_axis(fig, stats):
    """Show the whole period with plain '26 Oct' dates, a tick per day / week / month."""
    days = stats["days"]
    step = DAY_MS if days <= 14 else 7 * DAY_MS if days <= 90 else "M1"
    start, end = pd.Timestamp(stats["start"]), pd.Timestamp(stats["end"])
    fig.update_xaxes(
        title=None, tickformat="%-d %b", dtick=step, tick0=start, hoverformat="%a %-d %b",
        range=[start - pd.Timedelta(hours=12), end + pd.Timedelta(hours=12)],
    )


def page_stats():
    st.header("Stats")
    periods = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "All time": None}
    period = st.radio("Period", list(periods), index=1, horizontal=True)

    # Same numbers the assistant's get_stats tool reports.
    stats = get_stats(periods[period])
    entries = entries_between(stats["start"], stats["end"])
    entries["date"] = pd.to_datetime(entries["date"])
    for col in ("mood", "sleep", "energy"):
        entries[col] = pd.to_numeric(entries[col])

    # --- Averages ---
    def fmt(value, unit=""):
        return f"{value:.1f}{unit}" if value is not None else "–"

    a, b, c = st.columns(3)
    a.metric("Avg mood", fmt(stats["avg_mood"]))
    b.metric("Avg sleep", fmt(stats["avg_sleep"], " h"))
    c.metric("Avg energy", fmt(stats["avg_energy"]))

    # --- Line charts ---
    if entries.empty:
        st.info("No entries in this period yet.")
    else:
        # Mood and energy share a 1-5 scale, so they go on the same chart.
        fig = px.line(
            entries, x="date", y=["mood", "energy"], markers=True,
            title="Mood & energy", labels={"value": "Score (1-5)", "variable": ""},
        )
        fig.update_yaxes(range=[0.5, 5.5], dtick=1, tickformat="d")
        _date_axis(fig, stats)
        st.plotly_chart(fig, width="stretch")

        # Whole hours from 0, so a few nights of similar sleep don't zoom into 7.1, 7.2...
        top = max(10, int(entries["sleep"].max() or 0) + 2)
        fig = px.line(entries, x="date", y="sleep", markers=True, title="Sleep",
                      labels={"sleep": "Hours"})
        fig.update_yaxes(range=[0, top], dtick=1 if top <= 12 else 2, tickformat="d")
        _date_axis(fig, stats)
        st.plotly_chart(fig, width="stretch")

    # --- Habit completion % ---
    if stats["habit_completion_pct"]:
        completion = pd.DataFrame(
            {"habit": list(stats["habit_completion_pct"]), "percent": list(stats["habit_completion_pct"].values())}
        )
        fig = px.bar(completion, x="habit", y="percent", text_auto=".0f",
                     title=f"Habit completion ({stats['days']} days)",
                     labels={"percent": "% of days", "habit": ""})
        fig.update_yaxes(range=[0, 100], dtick=20, ticksuffix="%")
        st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Arete", page_icon=str(Path(__file__).parent / "Images" / "Logo.jpg"), layout="wide")

# All of the app's own CSS goes out first, so nothing jumps around as the page draws:
# the same top padding on every page, the sidebar nav restyled as large full-width
# items (radio circles hidden, current page with an accent bar), and no dimming of
# the previous page while a rerun is in progress.
st.html(f"""<style>
.stMainBlockContainer {{ padding-top: 4rem; padding-bottom: 1.5rem; }}
[data-stale="true"] {{ opacity: 1 !important; transition: none !important; }}
[data-testid="stElementContainer"]:has(#arete-ready) {{ display: none; }}   /* no stray gap */
[data-testid="stSidebar"] [data-testid="stElementContainer"]:has([data-testid="stRadio"]),
[data-testid="stSidebar"] [data-testid="stRadio"],
[data-testid="stSidebar"] [data-testid="stRadioGroup"] > div {{ width: 100% !important; }}
[data-testid="stSidebar"] [data-testid="stRadioGroup"] {{
    display: flex; flex-direction: column; align-items: stretch; gap: 6px; width: 100%;
}}
[data-testid="stSidebar"] [data-testid="stRadioOption"] {{
    box-sizing: border-box; align-self: stretch;
    width: 100%; margin: 0; padding: 14px 18px; border-radius: 10px;
    cursor: pointer; transition: background 0.12s;
}}
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div {{ width: 100%; }}
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child {{ display: none; }}
[data-testid="stSidebar"] [data-testid="stRadioOption"] p {{ font-size: 1.15rem; font-weight: 500; margin: 0; }}
[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover {{ background: rgba(128, 128, 128, 0.10); }}
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {{
    background: rgba(128, 128, 128, 0.16);
    box-shadow: inset 3px 0 0 {st.get_option("theme.primaryColor") or "#ff4b4b"};
}}
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] p {{ font-weight: 650; }}
</style>""")


def ready_marker():
    """Tells the desktop launcher the page has finished drawing (it then swaps the splash for the app)."""
    st.html('<div id="arete-ready" style="display:none"></div>')


init_db()

# First run (or reopened from Settings): the setup wizard instead of the app.
if settings_ui.needs_wizard():
    settings_ui.wizard()
    ready_marker()
    st.stop()

PAGES = {
    "Home": page_home,
    "Timetable": timetable.render_page,
    goals.PAGE: goals.render_page,
    daily_log.PAGE: daily_log.page_daily_log,
    "Stats": page_stats,
    "Settings": settings_ui.page_settings,
}

st.sidebar.title("Arete")
# Keyed so Home's goals "See all" can switch to the Goals page.
choice = st.sidebar.radio("Go to", list(PAGES), label_visibility="collapsed", key="page")
PAGES[choice]()

# The Claude chat bar, pinned to the bottom of every page (Home has it inline).
if choice != "Home":
    chat.render()


@st.fragment(run_every=15 * 60)
def _outlook_autosync():
    """Sync the Outlook calendar when the app opens and every 15 minutes after.
    It runs last, so the page shows the cached events straight away; the app
    reruns only if the sync actually brought new events."""
    if timetable.sync_outlook():
        st.rerun()


# The page is drawn (with the cached Outlook events), so the launcher can show it;
# the sync then runs, and only redraws if it brought something new.
ready_marker()
_outlook_autosync()
