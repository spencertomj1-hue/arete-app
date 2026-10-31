"""
"A new version is available": once a day (if switched on), ask GitHub for the
latest release of the public app and compare it with this copy's version.

The version comes from arete/VERSION, which the installer build writes
(packaging/arete.spec); a copy run from the source code has none ("dev") and is
updated with git instead, so it isn't checked. The request is a plain public
one to GitHub's API: no account, nothing about the user is sent. Settings ->
About can switch it off. No Streamlit here.
"""

import re
import time
from pathlib import Path

import requests

from arete import store

REPO = "spencertomj1-hue/arete-app"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
CHECK_EVERY = 24 * 60 * 60          # seconds
TIMEOUT = 8


def current_version():
    """This copy's version, e.g. "1.2.0", or "dev" when running from the source code."""
    try:
        return (Path(__file__).parent / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"


def _parts(version):
    """"v1.10.2" -> (1, 10, 2); anything odd -> ()."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(version or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(latest, current):
    a, b = _parts(latest), _parts(current)
    return bool(a and b and a > b)


def enabled():
    return bool(store.load_settings().get("update_check", True))


def check(force=False, get=requests.get):
    """Ask GitHub (at most once a day unless forced); remembers the answer in settings.json.
    Returns {"version", "url"} for the latest release, or None if it couldn't find out."""
    s = store.load_settings()
    if not force and time.time() - s.get("update_checked_at", 0) < CHECK_EVERY:
        return s.get("update_latest")
    try:
        r = get(LATEST_URL, timeout=TIMEOUT, headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        data = r.json()
        latest = {"version": str(data["tag_name"]).lstrip("v"), "url": data.get("html_url") or RELEASES_PAGE}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        store.save_settings(update_checked_at=time.time())       # offline etc.: try again tomorrow
        return s.get("update_latest")
    store.save_settings(update_checked_at=time.time(), update_latest=latest)
    return latest


def available():
    """The newer release to tell the user about ({"version", "url"}), or None.
    Nothing for a source copy, when switched off, or for a version the user dismissed."""
    if current_version() in ("dev", "0.0.0") or not enabled():       # 0.0.0: a test build
        return None
    latest = store.load_settings().get("update_latest")
    if not latest or not is_newer(latest.get("version"), current_version()):
        return None
    if store.load_settings().get("update_dismissed") == latest["version"]:
        return None
    return latest


def dismiss(version):
    store.save_settings(update_dismissed=version)
