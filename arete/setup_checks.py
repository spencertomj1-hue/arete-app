"""
Checks and actions behind the setup wizard and the Settings page, kept free of
UI code so they can be tested: is Claude installed and logged in, does the
microphone work, app settings (settings.json), backups and opening folders.
"""

import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np

from arete import assistant
from arete import store
from arete import system
from arete import voice

# Shown (with copy buttons) when Claude Code isn't installed or logged in.
# How to install Claude Code: PowerShell on Windows, Terminal elsewhere.
INSTALL_CMD = ("irm https://claude.ai/install.ps1 | iex" if system.IS_WINDOWS
               else "curl -fsSL https://claude.ai/install.sh | bash")
SHELL = "PowerShell" if system.IS_WINDOWS else "Terminal"
LOGIN_CMD = "claude auth login"

WHISPER_CACHE = Path.home() / ".cache" / "huggingface" / "hub" / "models--Systran--faster-whisper-small.en"


# ---------------------------------------------------------------------------
# Settings (settings.json in the data folder)
# ---------------------------------------------------------------------------

settings_path = store.settings_path
load_settings = store.load_settings
save_settings = store.save_settings


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def claude_status():
    """
    {found, path, bundled, logged_in, account, note}. `found` means the user has
    Claude Code installed (`bundled`: only the SDK's own copy is available).
    An API key (saved in Settings, or $ANTHROPIC_API_KEY) counts as logged in.
    Otherwise it asks `claude auth status`, which doesn't spend any tokens.
    """
    path = assistant.find_claude()
    bundled = bool(path) and path == assistant.bundled_claude()
    if assistant.api_key() and path:
        return {"found": not bundled, "path": path, "bundled": bundled, "logged_in": True, "account": "API key",
                "note": None}
    if not path or bundled:
        return {"found": False, "path": path, "bundled": bundled, "logged_in": False, "account": None,
                "note": "Claude isn't set up yet."}
    try:
        out = subprocess.run([path, "auth", "status", "--json"], capture_output=True, text=True, timeout=20,
                             env={**os.environ, **assistant._env(path)})
        info = json.loads(out.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return {"found": True, "path": path, "bundled": False, "logged_in": False, "account": None,
                "note": f"Couldn't check the login ({type(e).__name__})."}
    logged_in = bool(info.get("loggedIn"))
    return {"found": True, "path": path, "bundled": False, "logged_in": logged_in,
            "account": info.get("email") if logged_in else None,
            "note": None if logged_in else "Claude Code is installed but not logged in."}


# ---------------------------------------------------------------------------
# Microphone and speech model
# ---------------------------------------------------------------------------

def mic_test(seconds=3, transcribe=True):
    """
    Record for a few seconds. Returns {ok, level, text, error}: level is 0-100,
    text is what Whisper heard (the first run downloads the model).
    """
    try:
        voice.recorder.start()
        time.sleep(seconds)
        audio = voice.recorder.stop()
    except voice.VoiceError as e:
        return {"ok": False, "level": 0, "text": None, "error": str(e)}
    level = int(min(100, float(np.sqrt(np.mean(audio ** 2))) * 1000))
    text = None
    if transcribe:
        try:
            text = voice.transcribe(audio)
        except voice.VoiceError as e:
            return {"ok": True, "level": level, "text": None, "error": str(e)}
    return {"ok": True, "level": level, "text": text, "error": None}


def speech_model_downloaded():
    return WHISPER_CACHE.exists() and any(WHISPER_CACHE.rglob("model.bin"))


# ---------------------------------------------------------------------------
# Data: backups and folders
# ---------------------------------------------------------------------------

BACKUP_KEEP = 30                                   # automatic backups kept (one a day)
_AUTO = re.compile(r"life-(\d{4}-\d{2}-\d{2})-\d{6}\.db")   # a plain backup (not e.g. "-before-todos")


def default_backups_dir():
    return store.DATA_DIR / "backups"


def backups_dir():
    """settings.json "backup_folder" if chosen (e.g. in iCloud Drive / OneDrive), else data folder/backups."""
    chosen = store.load_settings().get("backup_folder")
    return Path(chosen).expanduser() if chosen else default_backups_dir()


def set_backups_dir(path):
    """Use this folder for backups (None = the default). Raises ValueError if it can't be written to."""
    if not path or not str(path).strip():
        store.save_settings(backup_folder=None)
        return default_backups_dir()
    folder = Path(str(path).strip()).expanduser()
    if not folder.is_absolute():
        raise ValueError("Give the whole path, e.g. " + str(Path.home() / "Backups"))
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".arete-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise ValueError(f"Arete can't save backups there ({e.strerror or e}).")
    store.save_settings(backup_folder=str(folder))
    return folder


def cloud_folders():
    """{"iCloud Drive": path, "OneDrive": path, ...} for cloud folders on this computer, as backup suggestions."""
    found = {}
    icloud = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if icloud.is_dir():
        found["iCloud Drive"] = icloud / "Arete backups"
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var) and Path(os.environ[var]).is_dir():
            found["OneDrive"] = Path(os.environ[var]) / "Arete backups"
            break
    for name in ("Google Drive", "My Drive", "Dropbox"):
        if (Path.home() / name).is_dir():
            found[name] = Path.home() / name / "Arete backups"
    return found


def auto_backup_settings():
    s = store.load_settings()
    return bool(s.get("auto_backup", True)), int(s.get("backup_keep", BACKUP_KEEP))


def auto_backup_if_due(today=None):
    """Once a day (if switched on): back up, then keep only the newest `backup_keep` plain backups.
    Returns the new backup's path, or None if one was already made today."""
    on, keep = auto_backup_settings()
    if not on or not store.DB_PATH.exists():
        return None
    today = (today or date.today()).isoformat()      # backups are named by the computer's clock
    if any((m := _AUTO.fullmatch(p.name)) and m.group(1) == today for p in list_backups()):
        return None
    made = backup_now()
    plain = [p for p in list_backups() if _AUTO.fullmatch(p.name)]      # newest first
    for old in plain[max(keep, 1):]:
        old.unlink(missing_ok=True)
    return made


def backup_now():
    """Copy the database into the backups folder (SQLite's safe online backup). Returns the file."""
    backups_dir().mkdir(parents=True, exist_ok=True)
    target = backups_dir() / f"life-{datetime.now():%Y-%m-%d-%H%M%S}.db"
    src, dst = sqlite3.connect(store.DB_PATH), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:                     # close both (a `with` block doesn't): Windows can't delete or move open files
        dst.close()
        src.close()
    return target


def list_backups():
    return sorted(backups_dir().glob("life-*.db"), reverse=True) if backups_dir().exists() else []


def open_folder(path):
    """Show a folder in Finder / File Explorer."""
    system.open_folder(path)
