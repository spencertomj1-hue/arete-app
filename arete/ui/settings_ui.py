"""
First-run setup wizard and the Settings page.

The wizard shows once (settings.json "wizard_done") and can be reopened from
Settings → About. Its Claude, Outlook and microphone steps are the same
sections the Settings page uses, so both stay in sync.

The Claude assistant is optional (settings.json "ai_enabled"): everything in
Arete works by hand, and switching it on adds the chat box and voice input.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from arete import assistant
from arete import outlook
from arete import setup_checks
from arete import store
from arete import system

STEPS = [("Welcome", ":material/waving_hand:"), ("Assistant", ":material/chat:"),
         ("Calendar", ":material/calendar_month:"), ("Microphone", ":material/mic:"), ("Done", ":material/check_circle:")]


# ---------------------------------------------------------------------------
# Shared sections
# ---------------------------------------------------------------------------

KEY_WAY, CODE_WAY = "Paste an API key (easiest)", "Use my Claude subscription (Claude Code)"


def claude_section(prefix):
    """Is Claude ready? If not, two ways to set it up: an Anthropic API key, or Claude Code."""
    status = st.session_state.get(f"{prefix}_claude") or setup_checks.claude_status()
    st.session_state[f"{prefix}_claude"] = status

    def recheck():
        st.session_state.pop(f"{prefix}_claude", None)

    if status["logged_in"]:
        how = "an Anthropic API key" if status["account"] == "API key" else f"Claude Code ({status['account']})"
        st.success(f"Claude is ready, using {how}.", icon=":material/check_circle:")
        if assistant.saved_api_key():
            if st.button("Remove the API key", key=f"{prefix}_key_forget", icon=":material/key_off:"):
                assistant.forget_api_key()
                recheck()
                st.rerun()
        return

    st.warning(status["note"], icon=":material/warning:")
    way = st.radio("How should Arete talk to Claude?", [KEY_WAY, CODE_WAY], key=f"{prefix}_claude_way")
    if way == KEY_WAY:
        st.markdown(
            "1. Go to [console.anthropic.com](https://console.anthropic.com), sign up or sign in, and add some credit "
            "(Anthropic charges per message; you can set a monthly limit there).\n"
            "2. Open **API keys** → **Create key**, and copy it.\n"
            "3. Paste it here. It's checked with Anthropic, then kept in your "
            f"{system.SECRET_STORE}, never in a file."
        )
        with st.form(f"{prefix}_key_form", clear_on_submit=True, border=False):
            key = st.text_input("Anthropic API key", type="password", placeholder="sk-ant-…")
            if st.form_submit_button("Save key", icon=":material/key:") and key.strip():
                ok, message = assistant.check_api_key(key)
                if ok:
                    assistant.save_api_key(key)
                    recheck()
                    st.rerun()
                st.error(message, icon=":material/error:")
    else:
        st.markdown("If you pay for Claude (Pro or Max), Arete can use that through **Claude Code**, Anthropic's "
                    f"command-line app. Open **{setup_checks.SHELL}** and run these one at a time (use the copy "
                    "button on each):")
        if not status["found"]:
            st.markdown("**1. Install Claude Code**")
            st.code(setup_checks.INSTALL_CMD, language="powershell" if system.IS_WINDOWS else "bash")
        st.markdown(f"**{'2' if not status['found'] else '1'}. Sign in** (opens your browser)")
        st.code(setup_checks.LOGIN_CMD, language="bash")
        if st.button("Check again", key=f"{prefix}_claude_check", icon=":material/refresh:"):
            recheck()
            st.rerun()
    st.caption("Everything else in Arete works without Claude.")


def outlook_guide(prefix):
    """Step-by-step guide to publishing an Outlook calendar, plus the paste box."""
    st.markdown(
        "1. Open [outlook.office.com](https://outlook.office.com) and sign in.\n"
        "2. Click the **Settings** cog → **Calendar** → **Shared calendars**.\n"
        "3. Under **Publish a calendar**, pick your calendar and **Can view all details**, then click **Publish**.\n"
        "4. Copy the **ICS** link (the one ending in **.ics**, not .html).\n"
        "5. Paste it below and press **Connect**."
    )
    st.info("Anyone who has this link can see your calendar, so keep it private. To revoke it, go back to "
            "Shared calendars and click **Unpublish** (or **Reset links** to get a new one).",
            icon=":material/visibility:")

    with st.form(f"{prefix}_outlook_form", clear_on_submit=True, border=False):
        pasted = st.text_input("ICS link", placeholder="https://outlook.office365.com/…/calendar.ics",
                               type="password", help="Hidden so the link isn't shown on screen.")
        go = st.form_submit_button("Connect", type="primary", icon=":material/link:")
    if go:
        try:
            url, note = outlook.check_link(pasted)
            if note:
                st.info(note, icon=":material/swap_horiz:")
            with st.spinner("Checking the link with Outlook…"):
                found = outlook.try_link(url)
                outlook.connect(url, found["data"])
            st.session_state[f"{prefix}_found"] = found
        except outlook.LinkError as e:
            st.error(str(e), icon=":material/error:")
        except outlook.SyncError as e:
            st.error(outlook.friendly(e), icon=":material/error:")
        except Exception as e:
            st.error(f"That didn't work: the link didn't give a calendar Arete can read ({type(e).__name__}).",
                     icon=":material/error:")

    found = st.session_state.get(f"{prefix}_found")
    if found and outlook.ics_url():
        st.success(f"Connected. Found {found['count']} event{'s' if found['count'] != 1 else ''} in the next 2 weeks.",
                   icon=":material/check_circle:")
        for ev in found["preview"]:
            st.markdown(f"- **{ev['title']}** · {ev['when']}")


def calendar_status(prefix):
    """Connected? Last sync, Sync now, Disconnect."""
    if not outlook.ics_url():
        st.markdown(":material/link_off: **Not connected.** Follow the steps below to show your Outlook "
                    "calendar in the timetable.")
        return False
    status = st.session_state.get("outlook_status") or {}
    st.markdown(f":material/check_circle: **Connected** · last synced {outlook.ago(outlook.last_synced())}")
    if status.get("error"):
        st.caption(f"Last attempt failed: {status['error']}")
    a, b, _ = st.columns([1, 1, 3])
    if a.button("Sync now", key=f"{prefix}_sync", icon=":material/sync:"):
        st.session_state["outlook_status"] = outlook.sync()
        st.rerun()
    if b.button("Disconnect", key=f"{prefix}_disconnect", icon=":material/link_off:"):
        st.session_state[f"{prefix}_confirm_disconnect"] = True
    if st.session_state.get(f"{prefix}_confirm_disconnect"):
        st.warning(f"Disconnect removes the link from the {system.SECRET_STORE} and the imported events from the timetable. "
                   "Your own blocks aren't touched.")
        c, d, _ = st.columns([1, 1, 3])
        if c.button("Yes, disconnect", key=f"{prefix}_disconnect_yes", type="primary"):
            outlook.disconnect()
            st.session_state.pop("outlook_status", None)
            st.session_state.pop(f"{prefix}_found", None)
            st.session_state[f"{prefix}_confirm_disconnect"] = False
            st.rerun()
        if d.button("Cancel", key=f"{prefix}_disconnect_no"):
            st.session_state[f"{prefix}_confirm_disconnect"] = False
            st.rerun()
    return True


def mic_section(prefix):
    st.markdown("Click the mic in the chat bar to speak instead of typing. The first time, your computer may ask whether "
                f"**Arete may use the microphone**: click **Allow**. Your voice is turned into text on this {system.COMPUTER} "
                "and never uploaded.")
    if not setup_checks.speech_model_downloaded():
        st.caption("The first test downloads the speech model (about 470 MB), so it can take a minute.")
    if st.button("Test microphone", key=f"{prefix}_mic", icon=":material/mic:"):
        with st.spinner("Recording for 3 seconds… say something!"):
            result = setup_checks.mic_test()
        st.session_state[f"{prefix}_mic_result"] = result
    result = st.session_state.get(f"{prefix}_mic_result")
    if result:
        if not result["ok"]:
            st.error(result["error"], icon=":material/mic_off:")
        elif result["text"]:
            st.success(f"Heard you: “{result['text']}”", icon=":material/check_circle:")
        else:
            st.warning(result["error"] or "The mic works, but no words were recognised.", icon=":material/warning:")


# ---------------------------------------------------------------------------
# First-run wizard
# ---------------------------------------------------------------------------

def ai_switch(prefix):
    """The on/off switch for the optional Claude assistant; True if it's on."""
    on = store.ai_enabled() if "ai_enabled" in store.load_settings() else prefix != "wiz"
    new = st.toggle("Use the Claude assistant", value=on, key=f"{prefix}_ai_on",
                    help="Adds a chat box (and voice input) where you can ask Claude to make changes for you. "
                         "Everything also works without it.")
    if new != store.load_settings().get("ai_enabled"):
        store.save_settings(ai_enabled=new)
    return new


AI_PITCH = ("Arete works fully by hand. Optionally, switch on the **Claude assistant** to get a chat box "
            "where you type (or say) things like *\"I went to the gym 6 till 7\"*, *\"fit 6 hours of revision "
            "into next week\"* or *\"had coffee with Sam\"*, and Claude makes the changes for you. "
            "It needs an Anthropic API key or a Claude subscription, and the messages you send go to Anthropic.")


def needs_wizard():
    return st.session_state.get("wizard_open") or not setup_checks.load_settings().get("wizard_done")


def _go(step):
    if step == 3 and not store.ai_enabled():          # the microphone is only for the assistant
        step = 4 if st.session_state.get("wizard_step", 0) < 3 else 2
    st.session_state["wizard_step"] = step


def _finish():
    if "ai_enabled" not in setup_checks.load_settings():
        setup_checks.save_settings(ai_enabled=False)  # skipped the question: the assistant stays off
    setup_checks.save_settings(wizard_done=True)
    st.session_state["wizard_open"] = False
    st.session_state["wizard_step"] = 0


def wizard():
    # Full-screen: no sidebar while setting up.
    st.html("<style>[data-testid='stSidebar'], [data-testid='stSidebarCollapsedControl'] { display: none; }</style>")
    step = st.session_state.setdefault("wizard_step", 0)
    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        name, icon = STEPS[step]
        st.caption(f"Setup · step {step + 1} of {len(STEPS)}")
        st.progress((step + 1) / len(STEPS))
        if step == 0:
            st.title("Welcome to Arete", anchor=False)
            st.markdown(
                "Arete keeps track of your days:\n\n"
                "- :material/calendar_view_week: a **timetable** of what you planned and what you actually did\n"
                "- :material/mood: a **daily log** of mood, sleep, energy and water, a journal, and your **habits**\n"
                "- :material/checklist: **to-dos** and **goals**, and a map of the **people** you know\n"
                "- :material/chat: optionally, a **Claude assistant** that can update it all for you\n\n"
                f":material/lock: **Your data stays on your {system.COMPUTER}.** Nothing is uploaded (unless you switch on "
                "the assistant, which sends your messages to Claude).\n\nThis takes about two minutes. You can skip "
                "anything and set it up later in **Settings**."
            )
        elif step == 1:
            st.header(f"{icon} Claude assistant (optional)", anchor=False)
            st.markdown(AI_PITCH)
            if ai_switch("wiz"):
                claude_section("wiz")
        elif step == 2:
            st.header(f"{icon} Outlook calendar (optional)", anchor=False)
            st.markdown("Show your Outlook events (classes, meetings) in the timetable. Arete only reads them.")
            if not calendar_status("wiz"):
                outlook_guide("wiz")
        elif step == 3:
            st.header(f"{icon} Microphone", anchor=False)
            mic_section("wiz")
        else:
            st.title("You're all set", anchor=False)
            st.markdown("Arete opens on **Home**: today's agenda and timetable, your to-dos, habits and goals"
                        + (", and the box to ask Claude" if store.ai_enabled() else "") + ". "
                        "Everything from this setup is in **Settings** if you want to change it"
                        + ("" if store.ai_enabled() else ", including switching on the Claude assistant") + ".")

        st.divider()
        back, skip, nxt = st.columns([1, 1, 1])
        if step > 0:
            back.button("Back", key="wiz_back", on_click=_go, args=(step - 1,), icon=":material/arrow_back:")
        if step == len(STEPS) - 1:
            nxt.button("Start using Arete", key="wiz_finish", type="primary", on_click=_finish, width="stretch")
        else:
            if 0 < step < len(STEPS) - 1:
                skip.button("Skip for now", key="wiz_skip", on_click=_go, args=(step + 1,))
            nxt.button("Next" if step else "Get started", key="wiz_next", type="primary", on_click=_go,
                       args=(step + 1,), width="stretch", icon=":material/arrow_forward:")


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

def reminders_section():
    """Desktop notifications before Plan blocks and Outlook events (arete/notify.py)."""
    from arete import notify
    on, minutes = notify.settings()
    st.write(f"A notification on your {system.COMPUTER} shortly before each block in your Plan "
             "(and each Outlook event) starts, while Arete is open.")
    new_on = st.toggle("Remind me before blocks start", value=on, key="remind_on")
    new_minutes = st.number_input("Minutes before", 0, 60, minutes, step=1, key="remind_minutes",
                                  disabled=not new_on, help="0 = when the block starts.")
    if (new_on, new_minutes) != (on, minutes):
        store.save_settings(reminders=new_on, reminder_minutes=int(new_minutes))
        st.toast("Saved", icon=":material/check_circle:")
    if st.button("Send a test notification", icon=":material/notifications_active:"):
        if system.notify("Arete", "Reminders are working."):
            st.success("Sent. If nothing popped up, check your system's notification settings"
                       + (" (System Settings → Notifications → Script Editor)." if system.IS_MAC else "."))
        else:
            st.error(f"Couldn't show a notification on this {system.COMPUTER}.")


def appearance_section():
    from arete.ui import appearance
    st.subheader("Appearance", anchor=False)
    current = appearance.mode()
    picked = st.segmented_control("Theme", appearance.MODES, default=current, key="set_theme",
                                  help="System follows your computer's light or dark mode.")
    if picked and picked != current:
        appearance.set_mode(picked)
        st.rerun()                               # the sync script then switches the theme
    size = appearance.text_size()
    new_size = st.segmented_control("Text size", list(appearance.SIZES), default=size, key="set_text_size")
    if new_size and new_size != size:
        appearance.set_text_size(new_size)
        st.rerun()
    st.markdown("The quick brown fox jumps over the lazy dog: this is how text looks at this size.")


def region_section():
    """Time zone (and, below, how dates, times and units are shown)."""
    import zoneinfo
    st.subheader("Time zone")
    st.caption("Decides what \"today\" is, and the times of your Outlook events.")
    chosen = store.load_settings().get("timezone")
    computer = store.computer_zone_name()
    own = st.toggle(f"Use my computer's time zone ({computer})", value=not chosen, key="region_tz_auto")
    if own and chosen:
        store.save_settings(timezone=None)
        st.rerun()
    if not own:
        zones = sorted(z for z in zoneinfo.available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))
        current = chosen or computer
        pick = st.selectbox("Time zone", zones, index=zones.index(current) if current in zones else None,
                            placeholder="Type a city, e.g. New_York", key="region_tz")
        if pick and pick != chosen:
            store.save_settings(timezone=pick)
            st.rerun()
    now = store.local_now()
    st.markdown(f"It's **{now:%A} {now.day} {now:%B}, {now:%H:%M}** in {store.zone_name().replace('_', ' ')}.")


def page_settings():
    st.header("Settings", anchor=False)
    ai = store.ai_enabled()
    names = [":material/palette: Appearance", ":material/public: Region", ":material/calendar_month: Calendar",
             ":material/chat: Assistant",
             ":material/notifications: Reminders", ":material/database: Data", ":material/info: About"]
    if ai:
        names.insert(4, ":material/mic: Voice")       # voice input is for the assistant
    tabs = dict(zip(["look", "region", "cal", "claude", "mic", "remind", "data", "about"] if ai else
                    ["look", "region", "cal", "claude", "remind", "data", "about"], st.tabs(names)))
    look, region, cal, claude, mic, remind, data, about = (
        tabs.get(k) for k in ("look", "region", "cal", "claude", "mic", "remind", "data", "about"))
    with look:
        appearance_section()
    with region:
        region_section()
    with cal:
        st.subheader("Outlook calendar", anchor=False)
        connected = calendar_status("set")
        if connected:
            with st.expander("Connect a different calendar", icon=":material/link:"):
                outlook_guide("set")
        else:
            outlook_guide("set")
    with claude:
        st.subheader("Claude assistant", anchor=False)
        st.markdown(AI_PITCH)
        if ai_switch("set") != ai:
            st.rerun()                                   # show or hide the chat box straight away
        if ai:
            claude_section("set")
    if mic:
        with mic:
            st.subheader("Voice input", anchor=False)
            mic_section("set")
    with remind:
        st.subheader("Reminders", anchor=False)
        reminders_section()
    with data:
        _data_section()
    with about:
        st.subheader("About Arete", anchor=False)
        st.markdown(
            "A personal life tracker: timetable, daily log and journal, habits, to-dos, goals, your network, "
            "stats, and an optional Claude assistant. "
            f"Built with Streamlit and SQLite; runs entirely on your {system.COMPUTER}.\n\n"
            "Open source under the **MIT licence**."
        )
        if st.button("Run the setup wizard again", icon=":material/restart_alt:"):
            st.session_state["wizard_open"] = True
            st.session_state["wizard_step"] = 0
            st.rerun()


def _data_section():
    st.subheader("Where your data lives", anchor=False)
    st.markdown(f":material/folder: `{store.DATA_DIR}`")
    a, b, c = st.columns(3)
    if a.button("Open data folder", icon=":material/folder_open:"):
        setup_checks.open_folder(store.DATA_DIR)
    if b.button("Back up now", icon=":material/backup:"):
        path = setup_checks.backup_now()
        st.toast(f"Backed up to {path.name}", icon=":material/check_circle:")
    if c.button("Open backups folder", icon=":material/inventory_2:"):
        setup_checks.open_folder(setup_checks.backups_dir())
    backups = setup_checks.list_backups()
    st.caption(f"{len(backups)} backup{'s' if len(backups) != 1 else ''}"
               + (f", latest {backups[0].name}" if backups else "") + ".")
    _backup_settings()

    st.subheader("Export", anchor=False)
    st.write("Your daily log and habits as one CSV: one row per date, one column per habit.")
    csv = store.load_all_data().to_csv(index=False)
    st.download_button("Download CSV", csv, file_name=f"arete_export_{store.local_today()}.csv",
                       mime="text/csv", icon=":material/download:")

    st.subheader("Import", anchor=False)
    st.write(
        f"Use the same format as the export: a `date` column (YYYY-MM-DD), optional "
        f"`mood`, `sleep`, `energy`, `water` (litres), `notes` columns, and `{store.HABIT_PREFIX}<name>` columns "
        f"containing true/false or 1/0."
    )
    uploaded = st.file_uploader("CSV file", type="csv")
    mode = st.radio("Import mode", ["Merge (update matching dates)", "Replace all existing data"])
    if uploaded and st.button("Import", icon=":material/upload:"):
        new = pd.read_csv(uploaded)
        if "date" not in new.columns:
            st.error("The CSV needs a `date` column.")
        else:
            store.save_all_data(new, replace=mode.startswith("Replace"))
            st.success(f"Imported {len(new)} rows.")

    st.subheader("Journal", anchor=False)
    entries = store.journal_entries()
    st.write(f"Your journal (the Daily Log's notes) as one text file, oldest first ({len(entries)} entr{'y' if len(entries) == 1 else 'ies'}).")
    text = "\n\n".join(f"# {e['date']}\n\n{e['text']}" for e in reversed(entries))
    st.download_button("Download journal", text, file_name=f"arete_journal_{store.local_today()}.md",
                       mime="text/markdown", icon=":material/download:", disabled=not entries)

    _csv_section(
        "To-dos", "todos", store.load_todos, store.save_todos, ["title", "period"],
        "`title`, `period` (daily, weekly or monthly) and `period_start` (YYYY-MM-DD, any day in the period); "
        "optional `id`, `notes`, `done`, `done_at`, `repeat`, `ord` and `created_at`.",
    )
    _csv_section(
        "Goals", "goals", store.load_goals, store.save_goals, ["title", "period", "target"],
        "`title`, `period` (monthly or yearly), `target` and `period_start` (YYYY-MM-DD, any day in the "
        "month or year); optional `id`, `unit`, `progress`, `habit` (a habit's name, to count its ticks), "
        "`ord` and `created_at`.",
    )


def _backup_settings():
    """Automatic daily backups, and where backups go."""
    st.subheader("Automatic backups", anchor=False)
    on, keep = setup_checks.auto_backup_settings()
    new_on = st.toggle("Back up automatically once a day", value=on, key="backup_auto",
                       help=f"While Arete is open. Keeps the newest {keep}; older ones are deleted.")
    new_keep = st.columns([1, 3])[0].number_input("Backups to keep", 3, 365, keep, step=1, key="backup_keep",
                                                  disabled=not new_on)
    if (new_on, new_keep) != (on, keep):
        store.save_settings(auto_backup=new_on, backup_keep=int(new_keep))
        st.toast("Saved", icon=":material/check_circle:")

    folder = setup_checks.backups_dir()
    st.markdown(f"Backups go to :material/folder: `{folder}`")
    clouds = setup_checks.cloud_folders()
    if clouds:
        st.caption("Keep them in a cloud folder so they survive a lost or broken computer:")
        cols = st.columns(len(clouds) + 1)
        for col, (name, path) in zip(cols, clouds.items()):
            if col.button(f"Use {name}", key=f"backup_cloud_{name}", icon=":material/cloud:", disabled=path == folder):
                setup_checks.set_backups_dir(path)
                st.rerun()
    with st.form("backup_folder_form", border=False):
        a, b = st.columns([4, 1], vertical_alignment="bottom")
        typed = a.text_input("Or another folder", placeholder=str(Path.home() / "Backups" / "Arete"),
                             help="The whole path to a folder. It's created if needed.")
        if b.form_submit_button("Use this folder"):
            try:
                setup_checks.set_backups_dir(typed)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    if folder != setup_checks.default_backups_dir():
        if st.button("Back to the default folder", key="backup_default", icon=":material/undo:"):
            setup_checks.set_backups_dir(None)
            st.rerun()


def _csv_section(name, slug, load, save, required, columns):
    """Export / import for to-dos or goals: their own CSV, one row per item."""
    st.subheader(name, anchor=False)
    st.write(f"{name} export and import as their own CSV, one row per item.")
    st.download_button(f"Download {name.lower()} CSV", load().to_csv(index=False),
                       file_name=f"arete_{slug}_{store.local_today()}.csv", mime="text/csv", icon=":material/download:")
    st.write(f"To import, use the same columns: {columns} A row whose `id` already exists updates it.")
    uploaded = st.file_uploader(f"{name} CSV file", type="csv", key=f"{slug}_csv")
    mode = st.radio(f"{name} import mode", ["Merge (update matching ids)", f"Replace all existing {name.lower()}"],
                    key=f"{slug}_mode")
    if uploaded and st.button(f"Import {name.lower()}", icon=":material/upload:"):
        new = pd.read_csv(uploaded)
        if not set(required) <= set(new.columns):
            st.error("The CSV needs " + ", ".join(f"`{c}`" for c in required) + " columns.")
        else:
            count = save(new, replace=mode.startswith("Replace"))
            st.success(f"Imported {count} {name.lower()}.")
