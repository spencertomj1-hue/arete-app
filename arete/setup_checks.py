"""
Checks and actions behind the setup wizard and the Settings page, kept free of
UI code so they can be tested: is Claude installed and logged in, does the
microphone work, app settings (settings.json), backups and opening folders.
"""

import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime
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

def backups_dir():
    return store.DATA_DIR / "backups"


def backup_now():
    """Copy the database into the backups folder (SQLite's safe online backup). Returns the file."""
    backups_dir().mkdir(parents=True, exist_ok=True)
    target = backups_dir() / f"life-{datetime.now():%Y-%m-%d-%H%M%S}.db"
    with sqlite3.connect(store.DB_PATH) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return target


def list_backups():
    return sorted(backups_dir().glob("life-*.db"), reverse=True) if backups_dir().exists() else []


def open_folder(path):
    """Show a folder in Finder / File Explorer."""
    system.open_folder(path)
