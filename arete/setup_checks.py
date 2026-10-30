"""
Checks and actions behind the setup wizard and the Settings page, kept free of
UI code so they can be tested: is Claude installed and logged in, does the
microphone work, app settings (settings.json), backups and opening folders.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from arete import assistant
from arete import store
from arete import voice

# Shown (with copy buttons) when Claude Code isn't installed or logged in.
INSTALL_CMD = "curl -fsSL https://claude.ai/install.sh | bash"
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
    {found, path, logged_in, account, note}. Uses `claude auth status`, which
    doesn't spend any tokens. An ANTHROPIC_API_KEY counts as logged in.
    """
    path = assistant.find_claude()
    if not path:
        return {"found": False, "path": None, "logged_in": False, "account": None,
                "note": "Claude Code isn't installed (or Arete can't find it)."}
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"found": True, "path": path, "logged_in": True, "account": "API key",
                "note": "Using the ANTHROPIC_API_KEY from your environment."}
    try:
        out = subprocess.run([path, "auth", "status", "--json"], capture_output=True, text=True, timeout=20,
                             env={**os.environ, **assistant._env(path)})
        info = json.loads(out.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return {"found": True, "path": path, "logged_in": False, "account": None,
                "note": f"Couldn't check the login ({type(e).__name__})."}
    logged_in = bool(info.get("loggedIn"))
    return {"found": True, "path": path, "logged_in": logged_in,
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


def open_in_finder(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    if shutil.which("open"):
        subprocess.run(["open", str(path)], check=False)
