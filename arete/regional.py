"""
Regional settings: which day weeks start on, 12- or 24-hour clock, how dates are
written, and litres or US fl oz. All in settings.json, read here; no Streamlit.

Data is always stored the same way (times "HH:mm", dates ISO, water in litres);
these only change how things are shown and typed in. The one exception is the
week start: weekly to-dos are stored under their week's first day, so changing it
moves them (store.set_week_start).
"""

from datetime import date, timedelta

from arete import store

WEEK_STARTS = store.WEEK_START
CLOCKS = ("24-hour", "12-hour")
DATE_FORMATS = {"DD/MM/YYYY": "day first", "MM/DD/YYYY": "month first", "YYYY-MM-DD": "year first"}
WATER_UNITS = ("litres", "US fl oz")
FL_OZ = 0.0295735          # litres in a US fluid ounce


def _get(key, allowed, default):
    value = store.load_settings().get(key, default)
    return value if value in allowed else default


def week_start():
    return store.week_start_name()


def clock():
    return _get("clock", CLOCKS, "24-hour")


def date_format():
    return _get("date_format", DATE_FORMATS, "DD/MM/YYYY")


def water_unit():
    return _get("water_unit", WATER_UNITS, "litres")


# --- Weeks ---

week_start_of = store.week_start_of


def weekday_names(short=True):
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] if short else \
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    k = WEEK_STARTS[week_start()]
    return names[k:] + names[:k]


# --- Times ---

def time(hhmm):
    """'13:30' -> '13:30' or '1:30 pm'."""
    if not hhmm or clock() == "24-hour":
        return hhmm
    h, m = map(int, hhmm.split(":"))
    suffix = "am" if h < 12 else "pm"
    h = h % 12 or 12
    return f"{h}:{m:02d} {suffix}" if m else f"{h} {suffix}"


def time_range(start, end):
    return f"{time(start)}–{time(end)}"


def times_in(text):
    """Every HH:mm in a piece of text in the chosen clock, e.g. an Outlook event's '09:05–10:00'."""
    import re
    return re.sub(r"\b(\d{2}:\d{2})\b", lambda m: time(m.group(1)), text or "") if text else text


# --- Dates ---

def day_month(d, weekday=True, year=False):
    """'Sat 31 Oct' (day first) or 'Sat Oct 31' (month first); with year: '... 2026'."""
    d = store._as_date(d)
    core = f"{d:%b} {d.day}" if date_format() == "MM/DD/YYYY" else f"{d.day} {d:%b}"
    return (f"{d:%a} " if weekday else "") + core + (f" {d.year}" if year else "")


def long_date(d):
    """'Saturday 31 October' or 'Saturday, October 31'."""
    d = store._as_date(d)
    return f"{d:%A}, {d:%B} {d.day}" if date_format() == "MM/DD/YYYY" else f"{d:%A} {d.day} {d:%B}"


def numeric_date(d, year=True):
    d = store._as_date(d)
    f = date_format()
    if f == "YYYY-MM-DD":
        return d.isoformat() if year else f"{d.month:02d}-{d.day:02d}"
    a, b = (d.month, d.day) if f == "MM/DD/YYYY" else (d.day, d.month)
    return f"{a:02d}/{b:02d}" + (f"/{d.year}" if year else "")


def input_format():
    """The `format` for st.date_input."""
    return date_format()


# --- Water ---

def water_out(litres):
    """Litres -> the user's unit (a number: litres to 2 places, ounces whole), or None."""
    if litres is None:
        return None
    return round(litres, 2) if water_unit() == "litres" else float(round(litres / FL_OZ))


def water_in(value):
    """The user's unit -> litres."""
    if value is None:
        return None
    return float(value) if water_unit() == "litres" else float(value) * FL_OZ


def water_text(litres):
    if litres is None:
        return "–"
    return f"{litres:g} L" if water_unit() == "litres" else f"{round(litres / FL_OZ):g} fl oz"


def water_short():
    return "L" if water_unit() == "litres" else "fl oz"
