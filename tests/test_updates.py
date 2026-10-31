"""Update check: compares this copy's version with GitHub's latest release (faked here: no network)."""

import importlib
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class Reply:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(str(self.status))

    def json(self):
        return self.data


@pytest.fixture
def updates(store, monkeypatch):
    from arete import updates as module
    module = importlib.reload(module)
    monkeypatch.setattr(module, "current_version", lambda: "1.1.1")
    return module


def test_versions(updates):
    assert updates.is_newer("1.2.0", "1.1.1") and updates.is_newer("v1.10.0", "1.9.9")
    assert not updates.is_newer("1.1.1", "1.1.1") and not updates.is_newer("junk", "1.0")


def test_check_and_banner_logic(updates, store):
    calls = []
    fake = lambda url, **kw: calls.append(url) or Reply({"tag_name": "v1.2.0", "html_url": "https://example.com/r"})
    assert updates.check(get=fake) == {"version": "1.2.0", "url": "https://example.com/r"}
    assert updates.check(get=fake) and len(calls) == 1                     # once a day
    assert updates.available()["version"] == "1.2.0"
    updates.dismiss("1.2.0")
    assert updates.available() is None                                     # "Not now" until the next one
    offline = lambda url, **kw: (_ for _ in ()).throw(requests.ConnectionError("offline"))
    assert updates.check(force=True, get=offline) == {"version": "1.2.0", "url": "https://example.com/r"}
    store.save_settings(update_check=False, update_dismissed=None)
    assert updates.available() is None                                     # switched off


def test_source_copies_are_not_checked(store):
    from arete import updates
    updates = importlib.reload(updates)
    if not (Path(updates.__file__).parent / "VERSION").exists():
        assert updates.current_version() == "dev"
    store.save_settings(update_latest={"version": "9.9.9", "url": "x"})
    assert updates.available() is None or updates.current_version() != "dev"


def test_banner_on_the_page(updates, store):
    store.save_settings(update_latest={"version": "1.2.0", "url": "https://example.com/r"})
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("Arete 1.2.0 is available" in m.value for m in at.markdown)
    at.button(key="update_dismiss").click().run()
    assert not any("is available" in m.value for m in at.markdown)
