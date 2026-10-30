# Arete — developer guide

How Arete is put together, the decisions behind it, and the pitfalls to know before changing it. For what the app does and how to install it, see the [README](README.md).

## 1. Running and testing

```bash
./setup.sh                                               # Python 3.13 check, .venv, pinned packages, builds Arete.app
.venv/bin/python desktop.py                              # the app in its own window (what Arete.app runs)
.venv/bin/streamlit run app.py                           # the app in a browser (development)
.venv/bin/python -m pytest -q -p no:logging tests        # the test suite
.venv/bin/python scripts/seed_demo.py                    # a demo data folder with fake data
ARETE_DATA_DIR=<folder> .venv/bin/streamlit run app.py   # run against any data folder
```

- **Restart after changes:** Streamlit only hot-reloads `app.py`. Any other module, and the components' JS/CSS (read once at import), need a restart.
- **Never test against real data:** tests and demos must set `ARETE_DATA_DIR` to a temporary folder (the pytest fixtures do).

## 2. Where things live

| What | Where |
|---|---|
| Code | the cloned folder. **Nothing user-specific is ever written here.** |
| User data | `~/Library/Application Support/Arete/` (or `$ARETE_DATA_DIR`): `life.db`, `settings.json`, `backups/`, `.outlook_cache.ics`, optional `arete_config.json`, and `agent/` (the Claude Code working folder) |
| Outlook calendar link | the macOS Keychain, service `Arete`, account `outlook-ics-url` (via `keyring`) |
| Speech model | `~/.cache/huggingface` (downloaded by faster-whisper on first use) |
| Arete.app | `/Applications/Arete.app` or `~/Applications/Arete.app`, built by `setup.sh`. Its launcher script holds the clone's path, written at build time. |

## 3. Files

```
app.py                       Streamlit entry: global CSS, wizard gate, sidebar nav, Home/Stats pages, chat bar, Outlook autosync
store.py                     ALL SQL; timetable painting rules; settings.json; stats. No Streamlit.
timetable.py                 Timetable page and the week/day grid component's Python side
timetable_component.js/.css  The grid (Streamlit components v2, shadow root)
daily_log.py                 Daily Log page (month calendar, day card, the day's habits, entry form) and Home's habits
todos.py                     Home's to-do cards: the to-do list component's Python side
todos_component.js/.css      The Reminders-style to-do lists (3 compact cards: day, week, month)
goals.py                     Goals page and Home's goal bars (native Streamlit widgets + an HTML bar)
calendar_component.js/.css   The month calendar component
tools.py                     What Claude can do: plain functions over store.py, previews for destructive changes
assistant.py                 Claude via claude-agent-sdk: finds the CLI, system prompt, in-process "arete" MCP server
chat.py, chat_component.*    The chat bar / Home ask box, and its flow (send → transcribe → Claude → rerun)
voice.py                     Microphone recording (sounddevice) and local transcription (faster-whisper)
outlook.py                   Read-only Outlook import: link checks, Keychain, fetch, recurrence expansion, sync
settings_ui.py               First-run wizard and the Settings page
setup_checks.py              Logic behind them: Claude status, mic test, backups, Finder
desktop.py, splash.html      Native window launcher with a splash screen (pywebview)
make_icon.py                 Builds Images/icon.png, Images/Arete.icns and Images/splash.png from Images/Logo.jpg
scripts/seed_demo.py         Demo data folder (never touches real data)
scripts/install-hooks.sh     gitleaks pre-commit hook
setup.sh                     One-command install
tests/                       pytest + AppTest; tests/fixtures/sample.ics is a fake calendar
```

## 4. Data model (`store.py`)

| Table | Contents |
|---|---|
| `entries` | Daily log: `date` (PK), `mood` 1–5, `sleep` hours, `energy` 1–5, `notes` |
| `habits`, `habit_log` | Habits, and one row per (habit, date) ticked. Cascade delete. |
| `tt_categories` | `id`, `name`, `colour` (#RRGGBB), `text_colour` (NULL = automatic), `grp` (heading in the key), `ord` |
| `tt_label_map` | Activity label → default category (case-insensitive lookups) |
| `tt_entries` | The user's timetable blocks: `id`, `date`, `track` (plan/actual), `start`/`"end"` ("HH:mm", end exclusive, may be "01:00"), `label`, `category_id`, `details` |
| `tt_outlook` | Imported Outlook events, **fully replaced on every sync**: `uid`, `date`, `start`/`"end"` (snapped; NULL if all-day), `title`, `location`, `all_day`, `exact` (real times), `description` |
| `tt_outlook_hidden` | Outlook occurrences the user removed or detached (kept across syncs) |
| `todos` | `id`, `title`, `notes`, `period` (daily/weekly/monthly), `period_start` (the day, its Monday or the 1st), `done`, `done_at`, `"repeat"`, `ord`, `created_at` |
| `todo_rollover` | Per period kind, the last period repeating to-dos were copied into (see §5b) |
| `goals` | `id`, `title`, `period` (monthly/yearly), `period_start`, `target`, `unit`, `progress` (kept by hand), `habit_id` (NULL = by hand; `ON DELETE SET NULL`), `ord`, `created_at` |

Notes on the schema:
- **New installs** are seeded with generic categories (groups "Life" and "Work") and activity defaults, in `DEFAULT_CATEGORIES` / `DEFAULT_LABEL_MAP`. Groups are read from the data (`store.groups()`), so a user's own groups work too.
- **`end` is an SQL keyword** and must be quoted.
- **Migrations:** columns added later are added by `_add_column` in `init_db`, so existing databases keep working. `_goals_became_todos` handles databases from before the to-do / goal split: their `goals` table (which held to-dos) is backed up to `backups/life-…-before-todos.db`, renamed to `todos` (with `goal_rollover` → `todo_rollover`), and its yearly to-dos dropped.
- **`settings.json`** holds `wizard_done` and `outlook_category`. Use `store.load_settings` / `save_settings`.

## 5. Timetable

- **Grid:** 36 half-hour slots from 07:00 to 01:00. Slots after midnight belong to the **previous** day (`slot_of` / `slot_time`).
- **`store.plan_paint` is the single source of truth for editing.** Filling slots s..e-1 trims or splits overlapped blocks, and touching blocks with the same label and category merge. It returns `(ops, affected)`, where `affected` = blocks with a different label/category that get overwritten (used for confirm previews).
  - `store.paint` applies it. In the Plan track it first **detaches** any Outlook event in the way, so those are trimmed like any block.
  - The grid's JS has a display-only copy (`planPaint`) so edits show instantly. If you change the rules, change both; `tests/test_store.py` pins the behaviour.
- **Grid ↔ Python protocol:**
  - The component sends ops with an `opId`: `paint`, `upsert`, `delete`, `hide_outlook` and `detach_outlook`.
  - Python applies them in `timetable.apply_grid_op` and acknowledges them in `data.acked`. The JS replays unacknowledged ops on top of incoming data, so a slow rerun never makes the grid flicker.
  - A paint carries the new block's id (`newId`), so both sides agree on it.
- **Views:** `render_page` is the week (editable). `render_today` is Home's single day: view only, with rows sized to fit the window (`fitToWindow`) and the key wrapped underneath.
- **Clicking a block** grows it and opens a details card (title, time, category, location, description), with Edit and Delete/Remove buttons except on Home. A drag paints, and a click with the eraser erases.
- **Block text** is black unless the fill is dark (`contrastText`, luminance threshold 90).

## 5b. To-dos and goals

Two different things:
- **To-dos** are tick-off lists for a day, a week or a month, shown only on Home.
- **Goals** are measurable targets for a month or a year, with progress bars, on the Goals page and on Home.

**Periods (shared):** all period maths is in `store.py` (`start_of_period`, `shift_period`, `end_of_period`, `period_title`), on Europe/London dates (`london_today()`). Weeks run Monday to Sunday, like the timetable.

**To-dos:**
- **Repeats:** `roll_todos()` runs whenever Home or a to-do tool loads. Once per period (remembered in `todo_rollover`), each title whose latest earlier copy has repeat on gets an unticked copy, unless the title is already there. A copy the user deletes therefore stays deleted, and turning repeat off on the latest copy stops it.
- **Carry over:** `carry_over(period, start)` copies unticked to-dos into the next period. It's in the store, but not currently in the UI.
- **Component (`todos_component.*`, key `home_todos`):** three compact cards with up to 6 to-dos each, then "N more", which expands in place. A container query stacks the cards when the column is under 480px.
- **Protocol:** the same as the timetable grid. The ops are `add`, `toggle`, `edit`, `repeat`, `move`, `delete` and `reorder`, each with an `opId`. `todos.apply_op` saves them, and they're acknowledged in `data.acked` (`todo_acked` in session state). An `add` carries the new to-do's id, so a resent op adds nothing. The JS calls them "goals" internally (`op.goal`, `section.goals`) from before the split.
- **Tick animation:** a ticked to-do stays in place for 650 ms (circle fills, title fades and strikes through), then folds into "Completed (n)", collapsed by default.
- **Editors:** the new-to-do, title and notes boxes are redrawn with their text, caret and focus after every rerun (`restoreEditor`), so typing is never lost.

**Goals:**
- **Value:** `goals_in` / `get_goal` add `value` (current progress) and `habit` (the linked habit's name). Linked to a habit, `value` is the number of days that habit was ticked within the period; otherwise it's the hand-kept `progress`. Deleting the habit unlinks the goal (back to `progress`).
- **Goals page:** two bordered sections (Monthly, Yearly), each with ‹ › and a "This month / This year" jump (`goals_start_<period>`). Each goal shows `value / target unit`, a percentage and a bar. Hand-kept goals have − / + (via `add_goal_progress`); every goal has a ⋯ popover to edit it (including the "Update it myself" / "Count a habit's ticks" choice) or delete it. "New goal" is a popover with the same form.
- **Bars:** HTML in the theme's primary colour (`st.progress` uses a different one), the same on both pages.
- **Home:** this month's and this year's goals as bars, with "See all" (a button whose callback sets the sidebar radio, `page`).

## 6. Outlook import (`outlook.py`)

- **Read only:** nothing is ever written to Outlook. The link is only in the Keychain. **Never log or display it.** `fetch()` replaces `requests`' exceptions (which contain the URL) with safe messages, and a test checks this.
- **Parsing:** `icalendar` + `recurring_ical_events` expand recurrences and their exceptions over a window from 14 days back to 8 weeks ahead. Times are converted to Europe/London. Windows timezone names are resolved (with a `WINDOWS_TO_OLSON` fallback).
- **Cancelled events are skipped:** `STATUS:CANCELLED`, and also titles starting "Canceled:"/"Cancelled:", because Outlook keeps meetings cancelled by someone else as CONFIRMED with that title.
- **Fitting to the grid:** `to_rows` intersects each event with every timetable day, snapping start down and end up to 30 minutes. All-day events get one row per day.
- **Descriptions:** `short_description` drops Teams/Zoom join boilerplate and cuts to about 300 characters.
- **User adjustments:**
  - **Remove** hides the occurrence (`tt_outlook_hidden`; uids are stable per occurrence, so it stays hidden).
  - **Edit**, or painting over one, **detaches** it into `tt_entries` as id `ol:<uid>`, with location and description in `details`.
  - **Claude** sees these events with ids `outlook:<uid>`.
- **Sync:**
  - **When:** an `st.fragment(run_every=15 min)` at the end of `app.py` runs once on open and then every 15 minutes, plus the ↻ button.
  - **Cache:** the last good `.ics` is cached in the data folder. Offline, the cache is re-parsed and the header says "Offline · events from X ago". With no link connected it says "Outlook not connected".
  - **Reruns:** the app reruns only if `store.outlook_signature()` changed.
- **Category:** set by `store.outlook_category()`, from settings, defaulting to "classes". Claude's `set_outlook_category` tool changes it.

## 7. Claude chat (`assistant.py`, `tools.py`, `chat.py`)

- **The agent:** `claude_agent_sdk.query` drives the local `claude` CLI. It uses the user's Claude Code login, or `ANTHROPIC_API_KEY` if set.
- **Isolation:**
  - `tools=[]` (no built-in tools) and `allowed_tools` = only `mcp__arete__*`, with `permission_mode="dontAsk"`.
  - `setting_sources=[]`, `strict_mcp_config=True` and `skills=[]`, so the user's own Claude config isn't loaded.
- **No chat on disk:** the CLI runs with `--no-session-persistence`. `chat.py` keeps the conversation in `st.session_state` and sends the last 12 messages as context (`_with_history`).
- **Finding the CLI:** `find_claude()` checks `arete_config.json`, then `$ARETE_CLAUDE_PATH`, `which`, the common install paths and nvm. Finder-launched apps get a bare PATH, so `_env()` adds PATH, USER and HOME; without USER/HOME the CLI can't read its Keychain login.
- **Async:** the SDK runs in a worker thread with its own event loop, so it never clashes with Streamlit's.
- **System prompt:** rebuilt every message, with the current Europe/London date/time, the timetable rules, the categories and the confirm rules.
- **Tools:**
  - `log_day`, `set_habit`, `add_habit`, `list_habits`
  - `add_block`, `update_block`, `delete_block`, `query_blocks`, `query_entries`, `get_stats`
  - `list_categories`, `edit_category`, `set_activity_default`, `set_outlook_category`
  - To-dos: `add_todo`, `complete_todo`, `uncomplete_todo`, `list_todos`, `delete_todo` (confirm first).
  - Goals: `add_goal`, `log_goal_progress`, `set_goal_progress`, `edit_goal`, `list_goals`, `delete_goal` (confirm first). Progress tools refuse habit-linked goals and point Claude at `set_habit`.
  - Both are found by id or title in the current periods: exact title, then part of a title, then the most shared words ("finish problem sheet" → "Problem sheet 4"). Several matches is an error that lists them, so Claude asks.
  - The prompt tells Claude that "today's goals" / "this week's goals" mean to-dos, since goals are only monthly or yearly.
- **Confirm-first:** anything that deletes data or overwrites/trims other data returns `Result(pending=Pending(summary, run))`. The UI shows Confirm/Cancel, and `run()` re-runs the tool with `confirmed=True` against current data.
- **Chat UI:** the bar is fixed to the bottom of the main column on every page except Home, where it's an inline box with the latest reply under it and the full history in a pop-up.
- **Flow:** callbacks set `chat_busy`. `chat.render()` draws first, does the slow work (transcribe, then Claude) and then calls `st.rerun()`, so changes show immediately.

## 8. Startup and smoothness

- **Two windows:** `desktop.py` shows the splash at once, then loads the app in a **hidden** window. When the page renders its `#arete-ready` marker (and at least 2 seconds have passed), it shows the app window and closes the splash.
- **Marker placement:** the marker comes before the Outlook autosync, so the network never delays startup.
- **Global CSS first:** `app.py` sends all global CSS as its first element (top padding, sidebar nav, no stale-element dimming, and hiding the marker's gap). Pages must not inject their own top padding.
- **Toolbar:** `.streamlit/config.toml` sets `client.toolbarMode = "minimal"`, along with `server.address = 127.0.0.1`, XSRF protection and no usage statistics.

## 9. Gotchas

- **The sidebar radio has `key="page"`.** Home's "See all" sets it from a component callback, which runs before the radio is drawn; don't set it anywhere after that.
- **NaN breaks components.** pandas turns SQL NULLs into NaN, which is invalid JSON. `store._read()` converts them to `None`; do the same for anything passed in a component's `data`.
- **`@font-face` doesn't work inside a component's shadow root.** Document-level fonts (like Streamlit's Material Symbols) do apply.
- **Fixed positioning:** the chat bar is `position: fixed` inside a component. That works because Streamlit's containers have no `transform`; a future Streamlit version could break it.
- **Empty element containers still add a 16px gap** in Streamlit layouts. Hide them if you add invisible elements.
- **Tests and the Keychain:** an autouse fixture swaps `outlook.keyring` for an in-memory fake. Modules that tests `importlib.reload` must have it swapped again; see `test_outlook.py` and `test_setup.py`.
- **UI tests:** for the headless browser or pywebview, run the server with `PYTHON_KEYRING_BACKEND=<module>.MemoryKeyring` and an empty `ARETE_DATA_DIR`.
- **The microphone permission prompt** for an unsigned app bundle launched via a shell script can vary by macOS version. A denied permission records silence, which `voice.Recorder` reports as such.

## 10. Ideas for later

- Timetable data in CSV export/import (to-dos and goals have their own CSVs).
- "Carry over unfinished" for to-dos in the UI (the store function exists).
- Streaming chat replies.
- Configurable grid hours (currently fixed at 07:00–01:00).
- Signing and notarising Arete.app.
