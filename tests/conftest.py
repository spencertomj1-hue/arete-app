"""
Every test gets its own empty database: ARETE_DB points store.py at a temp
file, so the real life.db is never touched.
"""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("ARETE_DATA_DIR", str(data))
    monkeypatch.setenv("ARETE_DB", str(data / "test.db"))
    from arete import store as store_module
    store_module = importlib.reload(store_module)
    store_module.init_db()
    # Most tests want the app itself, not the first-run wizard.
    (data / "settings.json").write_text('{"wizard_done": true}')
    yield store_module


@pytest.fixture(autouse=True)
def no_real_outlook(tmp_path, monkeypatch):
    """Tests never use the real Keychain, calendar link or cache (and never hit the network)."""
    from arete import outlook
    monkeypatch.setattr(outlook, "CACHE", tmp_path / "outlook-cache.ics")
    monkeypatch.setattr(outlook, "keyring", MemoryKeyring())
    from arete import assistant                  # nor a real Anthropic API key
    monkeypatch.setattr(assistant, "keyring", MemoryKeyring())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


class MemoryKeyring:
    """Stands in for the macOS Keychain in tests."""

    def __init__(self):
        self.items = {}

    def get_password(self, service, account):
        return self.items.get((service, account))

    def set_password(self, service, account, value):
        self.items[(service, account)] = value

    def delete_password(self, service, account):
        self.items.pop((service, account), None)
