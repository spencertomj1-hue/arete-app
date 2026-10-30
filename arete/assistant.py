"""
Claude for Arete: turns a chat message into tool calls on the user's data.

Uses claude-agent-sdk, which drives the local `claude` CLI, so it runs on the
user's Claude Code login (or ANTHROPIC_API_KEY if that's set in the environment;
the CLI prefers it automatically). Claude only gets the in-process "arete"
MCP tools below: no Bash, files or web.

Arete.app starts from Finder with a bare PATH, so the CLI is located
explicitly (see find_claude) and USER/HOME are passed through, because the CLI
needs them to read its login from the macOS Keychain. On Windows the CLI is
usually claude.exe in ~/.local/bin (or an npm claude.cmd).
"""

import asyncio
import getpass
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from claude_agent_sdk import (
    ClaudeAgentOptions, CLINotFoundError, ResultMessage, create_sdk_mcp_server, query, tool,
)

from arete import store
from arete import system
from arete import tools

CONFIG_PATH = store.DATA_DIR / "arete_config.json"   # optional: {"claude_path": "...", "model": "..."}
TZ = ZoneInfo("Europe/London")

# Working folder for the Claude Code process (it saves no transcripts: see _ask).
AGENT_DIR = store.DATA_DIR / "agent"

# Where the claude CLI usually lives when it isn't on PATH.
CLAUDE_CANDIDATES = [
    "~/.claude/local/claude", "~/.local/bin/claude", "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude", "~/.npm-global/bin/claude", "~/.bun/bin/claude",
] if not system.IS_WINDOWS else [
    "~/.local/bin/claude.exe", "~/.claude/local/claude.exe", "~/AppData/Roaming/npm/claude.cmd",
    "~/.bun/bin/claude.exe",
]

LOGIN_HELP = (f"Claude isn't logged in. Open {'PowerShell' if system.IS_WINDOWS else 'Terminal'}, run `claude`, and log in with /login. "
              "Then try again.")


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find_claude():
    """Path to the claude CLI, or None. arete_config.json / $ARETE_CLAUDE_PATH override the search."""
    override = load_config().get("claude_path") or os.environ.get("ARETE_CLAUDE_PATH")
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which("claude")
    if found:
        return found
    candidates = [Path(c).expanduser() for c in CLAUDE_CANDIDATES]
    candidates += sorted(Path.home().glob(".nvm/versions/node/*/bin/claude"), reverse=True)
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _env(claude_path):
    """Extra environment for the CLI: a usable PATH and the user's identity."""
    path_dirs = [str(Path(claude_path).parent)]
    if not system.IS_WINDOWS:
        path_dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    path = os.pathsep.join(dict.fromkeys(path_dirs + os.environ.get("PATH", "").split(os.pathsep)))
    user = os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()
    env = {"PATH": path, "USER": user, "LOGNAME": os.environ.get("LOGNAME") or user,
           "HOME": os.environ.get("HOME") or str(Path.home())}
    if system.IS_WINDOWS:
        env["USERPROFILE"] = os.environ.get("USERPROFILE") or str(Path.home())
    return env


# ---------------------------------------------------------------------------
# System prompt (rebuilt for every message so "today" is always right)
# ---------------------------------------------------------------------------

def system_prompt(now=None):
    now = now or datetime.now(TZ)
    return f"""You are the assistant built into Arete, the user's personal life-tracking app.
You change their data only through the arete tools. You have no other tools.

Right now it is {now:%A %d %B %Y, %H:%M} (Europe/London). Today's date is {now:%Y-%m-%d}.
Work out relative dates ("tomorrow", "next Tuesday", "last night") from this, and always pass
dates to tools as YYYY-MM-DD. Weeks start on Monday.

What the app tracks
- Daily log: per date, mood 1-5, sleep hours, energy 1-5 and free-text notes (log_day).
  Use append_notes=true to add to existing notes rather than replace them.
- Habits: named habits ticked per day, with streaks (set_habit, add_habit, list_habits).
  Only add a new habit if the user clearly asks for one.
- Timetable: every day has two tracks, "plan" (what they intend to do) and "actual"
  (what they really did). If they describe something that already happened ("I went to the gym
  from 6 to 7"), use actual; if it's an intention ("I'll study 2-4 tomorrow"), use plan.
  If it's unclear, ask.
  - Slots are 30 minutes, from 07:00 to 01:00 the next morning. Start and end must be on the
    hour or half hour. Times after midnight (00:00-01:00) belong to the PREVIOUS day's timetable,
    e.g. "Friday night until 1am" is date=Friday, end="01:00".
  - Use 24-hour times: "2pm" = "14:00". If am/pm is ambiguous, choose the sensible reading
    for that activity, or ask.
  - Adding a block over existing blocks trims or splits them (like painting over cells);
    touching blocks with the same label and category merge.
  - The category is picked from the activity label when there's a default for it; otherwise
    pass a category name. If no category fits, ask which one to use.
  - To change or delete a block, find its id with query_blocks first.
  - Categories and their colours, and which category an activity uses by default, are changed
    only by you (edit_category, set_activity_default); there are no editors in the app for them.
    Use hex colours (e.g. "make Gym dark green" -> #1B7F3B). Ask before creating a new category.
  - Events from the user's Outlook calendar are imported into the plan track. query_blocks lists them
    with ids starting "outlook:". The user can adjust them like their own blocks: update_block turns one
    into their own block and changes it, delete_block removes it from Arete's timetable (both need the
    user's confirmation), and adding a block over one trims it. None of this ever changes Outlook.

- To-dos: tick-off lists for a day, a week (Monday to Sunday) or a month (period "daily", "weekly" or
  "monthly"), shown on the Home page. A to-do belongs to the period containing its date (default today).
  Users often call these "goals" ("add go gym to this week's goals", "today's goals"): anything for a
  day or a week, or anything you tick off, is a to-do.
  - "add go gym to this week's goals" -> add_todo(title="Go gym", period="weekly"). Sentence-case titles.
  - "tick off finish problem sheet" -> complete_todo(todo="finish problem sheet"). To-dos are found by
    title in the current day/week/month (pass period or date to narrow it down, or the id from
    list_todos). If several match, ask which one. uncomplete_todo unticks.
  - repeat=true gives the to-do a fresh unticked copy every new period ("every week").
  - delete_todo needs the user's confirmation.

- Goals: measurable targets for a month or a year (period "monthly" or "yearly"), with a progress bar,
  e.g. "Gym: 12 sessions this month", "Read 20 books this year". Each has a target and an optional unit.
  - Progress is either updated by hand (log_goal_progress adds, e.g. "did 2 more gym sessions";
    set_goal_progress sets it), or counted automatically from a habit (habit="Gym": the number of days
    that habit was ticked in the period). For a habit-linked goal, tick the habit with set_habit instead.
  - "set a goal of 12 gym sessions this month" -> add_goal(title="Gym", target=12, unit="sessions").
    If a habit with a matching name exists, ask whether to count that habit or update it by hand.
  - edit_goal changes the title, target, unit or linked habit; delete_goal needs the user's confirmation.

- Network: the people the user knows, shown as a web on the Network page, coloured by group.
  Each person has a name, an optional description (who they are / how the user knows them),
  works_for (company, university, team) and a group (see find_people for the list).
  Groups can have sub-groups, e.g. "Northfield" containing "Physics Society" and "Robotics Lab"; in the
  web the user sits in the middle, each group branches off them, sub-groups branch off their group,
  and people branch off the group they're in. Put people in the most specific group that fits.
  - "add Physics Society and Robotics Lab under Northfield" -> add_group("Physics Society", inside="Northfield") etc.
    (add Northfield first if it doesn't exist). edit_group renames, recolours or moves a group;
    delete_group needs the user's confirmation.
  - "Sam works at Google, a friend from uni" -> add_person(name="Sam", works_for="Google",
    group="Friends", description="Friend from uni"). Pick the group that fits best; if none does, ask.
  - Connections between people are separate: connect_people(person, other, reason), e.g. "Sam knows
    Alex from football" -> connect_people("Sam", "Alex", reason="Football"). Add anyone new first.
    Everyone is already linked to the user in the web, so never connect someone to the user.
  - Use find_people to look someone up or check who exists before adding (avoid duplicates).
  - disconnect_people and delete_person need the user's confirmation.

{tools.list_categories().text}

Confirmations
Deleting, or overwriting/trimming existing data, never happens straight away: the tool returns
a preview and the user gets Confirm / Cancel buttons in the chat panel. When that happens, say
in one short line what will change if they confirm. Don't call the tool again for it.

Style
- Keep replies very short. After a change, confirm what changed in one line,
  e.g. "Added Gym 18:00–19:00 to Thursday's actual."
- Ask one short question when something is ambiguous instead of guessing.
- No markdown headings or tables; plain sentences.
"""


# ---------------------------------------------------------------------------
# Tools (thin wrappers around tools.py)
# ---------------------------------------------------------------------------

DATE = {"type": "string", "description": "YYYY-MM-DD"}
TIME = {"type": "string", "description": "HH:mm on the hour or half hour, 07:00-01:00"}
TRACK = {"type": "string", "enum": ["plan", "actual"]}


def _schema(required, **props):
    return {"type": "object", "properties": props, "required": list(required)}


TODO_PERIOD = {"type": "string", "enum": ["daily", "weekly", "monthly"]}
GOAL_PERIOD = {"type": "string", "enum": ["monthly", "yearly"]}
TODO_REF = _schema(["todo"], todo={"type": "string", "description": "the to-do's title (or part of it), or its id"},
                   period=TODO_PERIOD, date={**DATE, "description": "a day in the to-do's period, if not the current one"})
GOAL_REF = {"goal": {"type": "string", "description": "the goal's title (or part of it), or its id"},
            "period": GOAL_PERIOD, "date": {**DATE, "description": "a day in the goal's month/year, if not the current one"}}

TOOL_SPECS = [
    ("log_day", "Set mood/sleep/energy/notes for a date. Fields not given are left as they are.",
     _schema(["date"], date=DATE,
             mood={"type": "integer", "minimum": 1, "maximum": 5},
             sleep={"type": "number", "description": "hours slept"},
             energy={"type": "integer", "minimum": 1, "maximum": 5},
             notes={"type": "string"},
             append_notes={"type": "boolean", "description": "add to existing notes instead of replacing"}),
     tools.log_day),
    ("set_habit", "Tick (done=true) or untick (done=false) a habit on a date.",
     _schema(["date", "habit_name", "done"], date=DATE, habit_name={"type": "string"}, done={"type": "boolean"}),
     tools.set_habit),
    ("add_habit", "Create a new habit to track.", _schema(["name"], name={"type": "string"}), tools.add_habit),
    ("list_habits", "List habits with today's status and streaks.", _schema([]), tools.list_habits),
    ("add_block", "Add a timetable block. Category is optional; it's picked from the label when there's a default.",
     _schema(["date", "track", "start", "end", "label"], date=DATE, track=TRACK, start=TIME, end=TIME,
             label={"type": "string", "description": "the activity"},
             category={"type": "string", "description": "category name"},
             details={"type": "string", "description": "optional short description"}),
     tools.add_block),
    ("update_block", "Change a timetable block (or an Outlook event, id 'outlook:...'): label, category, "
                     "description, date, track, start or end. Get the id from query_blocks.",
     _schema(["block_id"], block_id={"type": "string"}, label={"type": "string"}, category={"type": "string"},
             date=DATE, track=TRACK, start=TIME, end=TIME, details={"type": "string"}),
     tools.update_block),
    ("delete_block", "Delete a timetable block, or remove an Outlook event (id 'outlook:...') from the timetable.",
     _schema(["block_id"], block_id={"type": "string"}), tools.delete_block),
    ("query_blocks", "List timetable blocks (with ids) between two dates, optionally one track.",
     _schema(["start_date", "end_date"], start_date=DATE, end_date=DATE, track=TRACK), tools.query_blocks),
    ("query_entries", "List daily log entries and ticked habits between two dates.",
     _schema(["start_date", "end_date"], start_date=DATE, end_date=DATE), tools.query_entries),
    ("get_stats", "Averages, habit completion % and timetable hours per category for a period.",
     _schema(["period"], period={"type": "string", "description": "'7d', '30d', '90d', 'all' or e.g. '14d'"}),
     tools.get_stats),
    ("list_categories", "List timetable categories (with colours) and the default category for common activities.",
     _schema([]), tools.list_categories),
    ("edit_category", "Change a timetable category's colour, name or group, or create a new category (create=true).",
     _schema(["name"], name={"type": "string", "description": "the category's current name"},
             colour={"type": "string", "description": "hex, e.g. #00B0F0"},
             new_name={"type": "string"}, group={"type": "string", "description": "a group in the key, e.g. 'Life'"},
             create={"type": "boolean", "description": "make a new category with this name"}),
     tools.edit_category),
    ("set_outlook_category", "Set which category (and so colour) Outlook calendar events use.",
     _schema(["category"], category={"type": "string"}), tools.set_outlook_category),
    ("add_todo", "Add a to-do to a day, week or month: the period containing `date` (default today).",
     _schema(["title", "period"], title={"type": "string"}, period=TODO_PERIOD,
             date={**DATE, "description": "YYYY-MM-DD, any day in the period (default today)"},
             repeat={"type": "boolean", "description": "a fresh copy every new period"},
             notes={"type": "string", "description": "optional short note shown under the title"}),
     tools.add_todo),
    ("complete_todo", "Tick off a to-do, found by title (or id) in the current periods.", TODO_REF, tools.complete_todo),
    ("uncomplete_todo", "Untick a to-do, found by title (or id) in the current periods.", TODO_REF,
     tools.uncomplete_todo),
    ("list_todos", "List to-dos (with ids) for the periods containing a date (default today).",
     _schema([], period=TODO_PERIOD, date=DATE), tools.list_todos),
    ("delete_todo", "Delete a to-do, found by title (or id) in the current periods.", TODO_REF, tools.delete_todo),
    ("add_goal", "Add a measurable goal for a month or year, e.g. 12 gym sessions. Link a habit to count it automatically.",
     _schema(["title", "target"], title={"type": "string"}, target={"type": "number"}, period=GOAL_PERIOD,
             unit={"type": "string", "description": "e.g. 'sessions', 'km', 'books' (optional)"},
             habit={"type": "string", "description": "a habit whose ticks count as progress (optional)"},
             date={**DATE, "description": "any day in the month/year (default today)"}),
     tools.add_goal),
    ("log_goal_progress", "Add to a goal's progress (amount defaults to 1; negative takes some off).",
     _schema(["goal"], **GOAL_REF, amount={"type": "number"}), tools.log_goal_progress),
    ("set_goal_progress", "Set a goal's progress to an exact number.",
     _schema(["goal", "value"], **GOAL_REF, value={"type": "number"}), tools.set_goal_progress),
    ("edit_goal", "Change a goal's title, target, unit, or linked habit (unlink_habit=true to update it by hand).",
     _schema(["goal"], **GOAL_REF, title={"type": "string"}, target={"type": "number"}, unit={"type": "string"},
             habit={"type": "string"}, unlink_habit={"type": "boolean"}),
     tools.edit_goal),
    ("list_goals", "List goals with progress and ids for the month and year containing a date (default today).",
     _schema([], period=GOAL_PERIOD, date=DATE), tools.list_goals),
    ("delete_goal", "Delete a goal, found by title (or id).", _schema(["goal"], **GOAL_REF), tools.delete_goal),
    ("add_person", "Add someone to the user's network.",
     _schema(["name"], name={"type": "string"}, description={"type": "string", "description": "who they are / how the user knows them"},
             works_for={"type": "string", "description": "company, university or team"},
             group={"type": "string", "description": "a network group, e.g. 'Friends'"}),
     tools.add_person),
    ("update_person", "Change a person's name, description, works_for or group (by name or id).",
     _schema(["person"], person={"type": "string"}, name={"type": "string"}, description={"type": "string"},
             works_for={"type": "string"}, group={"type": "string"}, clear_group={"type": "boolean"}),
     tools.update_person),
    ("connect_people", "Record that two people in the network know each other, and why.",
     _schema(["person", "other"], person={"type": "string"}, other={"type": "string"},
             reason={"type": "string", "description": "how they know each other, e.g. 'Work together at Google'"}),
     tools.connect_people),
    ("disconnect_people", "Remove the connection between two people.",
     _schema(["person", "other"], person={"type": "string"}, other={"type": "string"}), tools.disconnect_people),
    ("delete_person", "Remove someone from the network (and their connections).",
     _schema(["person"], person={"type": "string"}), tools.delete_person),
    ("add_group", "Add a network group, optionally inside another group as a sub-group (e.g. 'Physics Society' inside 'Northfield').",
     _schema(["name"], name={"type": "string"}, inside={"type": "string", "description": "the parent group's name"},
             colour={"type": "string", "description": "hex, e.g. #3E8EF7 (sub-groups default to their parent's)"}),
     tools.add_group),
    ("edit_group", "Rename, recolour or move a network group (inside another group, or top_level=true).",
     _schema(["group"], group={"type": "string"}, name={"type": "string"}, inside={"type": "string"},
             top_level={"type": "boolean"}, colour={"type": "string"}),
     tools.edit_group),
    ("delete_group", "Delete a network group; its people and sub-groups move up a level.",
     _schema(["group"], group={"type": "string"}), tools.delete_group),
    ("find_people", "List people in the network (with ids, groups and connections), optionally filtered by a search.",
     _schema([], query={"type": "string"}), tools.find_people),
    ("set_activity_default", "Set which category an activity label (e.g. 'Lecture') uses by default.",
     _schema(["label", "category"], label={"type": "string"}, category={"type": "string"}),
     tools.set_activity_default),
]


@dataclass
class TurnState:
    """What the tools did during one message."""
    pending: list = field(default_factory=list)   # tools.Pending previews awaiting Confirm
    changed: bool = False


def _make_server(state):
    def wrap(name, desc, schema, fn):
        @tool(name, desc, schema)
        async def handler(args):
            try:
                result = fn(**args)
            except tools.ToolError as e:
                return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
            except Exception as e:  # don't let a bug kill the conversation
                return {"content": [{"type": "text", "text": f"Internal error: {e}"}], "is_error": True}
            state.changed |= result.changed
            text = result.text
            if result.pending:
                state.pending.append(result.pending)
                text = (f"PREVIEW ONLY, nothing changed yet: {result.pending.summary}. "
                        "The user now sees Confirm / Cancel buttons for this. Tell them in one short line "
                        "what will change if they confirm. Don't call the tool again for it.")
            return {"content": [{"type": "text", "text": text}]}
        return handler

    return create_sdk_mcp_server("arete", tools=[wrap(*spec) for spec in TOOL_SPECS])


ALLOWED_TOOLS = [f"mcp__arete__{spec[0]}" for spec in TOOL_SPECS]


# ---------------------------------------------------------------------------
# Running a turn
# ---------------------------------------------------------------------------

@dataclass
class Turn:
    reply: str = ""
    pending: list = field(default_factory=list)
    changed: bool = False
    error: str | None = None


HISTORY_TURNS = 12   # recent messages sent along as context


def _with_history(text, history):
    """
    Claude Code's own session files are switched off (--no-session-persistence),
    so nothing is saved to disk. Instead the recent conversation goes in the prompt.
    """
    recent = [(role, msg) for role, msg in history if role in ("user", "assistant")][-HISTORY_TURNS:]
    if not recent:
        return text
    lines = "\n".join(f"{'User' if role == 'user' else 'You'}: {msg}" for role, msg in recent)
    return f"Conversation so far (for context):\n{lines}\n\nNew message from the user:\n{text}"


async def _ask(prompt, claude_path, state):
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    options = ClaudeAgentOptions(
        system_prompt=system_prompt(),
        mcp_servers={"arete": _make_server(state)},
        allowed_tools=ALLOWED_TOOLS,
        tools=[],                      # no built-in tools (Bash, files, web...)
        permission_mode="dontAsk",     # anything not allowed above is refused
        setting_sources=[], strict_mcp_config=True, skills=[],   # ignore the user's own Claude config
        cli_path=claude_path, cwd=str(AGENT_DIR), env=_env(claude_path),
        extra_args={"no-session-persistence": None},             # don't write the chat to disk
        max_turns=15, model=cfg.get("model"),
    )
    turn = Turn()
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            turn.reply = (message.result or "").strip()
    return turn


def _friendly(error):
    msg = str(error)
    low = msg.lower()
    if "not logged in" in low or "/login" in low or "authentication" in low or "oauth" in low:
        return LOGIN_HELP
    if "credit balance" in low or "invalid api key" in low:
        return f"The Anthropic API key was refused: {msg}"
    return f"Claude ran into a problem: {msg}"


def run_turn(text, history=()):
    """
    Send one chat message to Claude, with the recent conversation as context
    (history: (role, text) pairs). Blocks until done; safe to call from Streamlit.
    """
    claude_path = find_claude()
    if not claude_path:
        return Turn(error=(
            "Couldn't find the Claude Code CLI (`claude`). Install it (Settings → Claude shows how), "
            f"or set its path in {CONFIG_PATH} as {{\"claude_path\": \"/path/to/claude\"}}."))

    state = TurnState()
    try:
        # Run the SDK's asyncio code in its own thread with a fresh event loop,
        # so it never clashes with Streamlit's.
        with ThreadPoolExecutor(max_workers=1) as pool:
            turn = pool.submit(asyncio.run, _ask(_with_history(text, history), claude_path, state)).result(timeout=300)
    except CLINotFoundError:
        return Turn(error=f"Couldn't start Claude Code at {claude_path}. Check the path in {CONFIG_PATH}.")
    except Exception as e:
        return Turn(error=_friendly(e))
    turn.pending, turn.changed = state.pending, state.changed
    return turn
