"""
Undo for deletes and overwrites.

Before anything is deleted or overwritten, `checkpoint(label)` copies the whole
database (it's small) to undo.db in the data folder. The page then offers
"Undo" (ui/undo_bar.py) for a couple of minutes; `restore()` copies the snapshot
back. Only the latest change can be undone, and only while nothing else has been
saved since (otherwise restoring would also throw that away), which SQLite's
change counter in the file's header tells us. No Streamlit here.
"""

import json
import sqlite3
import time

from arete import store

KEEP_FOR = 120          # seconds the Undo stays on offer


def _snapshot():
    return store.DATA_DIR / "undo.db"


def _state_file():
    return store.DATA_DIR / "undo.json"


def _fingerprint():
    """SQLite's file change counter (header bytes 24-27, bumped by every committed write) and the size."""
    with open(store.DB_PATH, "rb") as f:
        header = f.read(28)
    return [int.from_bytes(header[24:28], "big"), store.DB_PATH.stat().st_size]


def _copy(src_path, dst_path):
    src, dst = sqlite3.connect(src_path), sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()             # closed explicitly: Windows can't replace open files
        src.close()


def _read():
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(state):
    _state_file().write_text(json.dumps(state), encoding="utf-8")


def checkpoint(label):
    """Call just before deleting or overwriting something: 'Deleted Gym', 'Replaced Tuesday's plan'..."""
    if not store.DB_PATH.exists():
        return
    _copy(store.DB_PATH, _snapshot())
    _write({"label": label, "at": time.time(), "after": None})


def pending():
    """The change that can be undone right now, as its label, or None."""
    state = _read()
    if not state or time.time() - state["at"] > KEEP_FOR or not _snapshot().exists():
        return None
    if state["after"] is None:              # first look after the change: remember what the database is like now
        state["after"] = _fingerprint()
        _write(state)
    elif state["after"] != _fingerprint():  # something else was saved since: undoing would lose it
        return None
    return state["label"]


def restore():
    """Undo the latest change. Returns its label, or None if it can't be undone any more."""
    label = pending()
    if label is None:
        return None
    _copy(_snapshot(), store.DB_PATH)
    dismiss()
    return label


def dismiss():
    _state_file().unlink(missing_ok=True)
    _snapshot().unlink(missing_ok=True)
