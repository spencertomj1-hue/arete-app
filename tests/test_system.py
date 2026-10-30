"""Platform differences (arete/system.py and the Claude CLI search), faked for each system."""

import os
from pathlib import Path

from arete import system


def as_system(monkeypatch, name):
    monkeypatch.setattr(system, "IS_MAC", name == "mac")
    monkeypatch.setattr(system, "IS_WINDOWS", name == "windows")


def test_data_folder_per_system(monkeypatch, tmp_path):
    as_system(monkeypatch, "mac")
    assert system.default_data_dir() == Path.home() / "Library" / "Application Support" / "Arete"
    as_system(monkeypatch, "windows")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert system.default_data_dir() == tmp_path / "Roaming" / "Arete"
    monkeypatch.delenv("APPDATA")
    assert system.default_data_dir() == Path.home() / "AppData" / "Roaming" / "Arete"
    as_system(monkeypatch, "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert system.default_data_dir() == tmp_path / "share" / "Arete"


def test_open_folder(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(system.subprocess, "run", lambda args, check: calls.append(args))
    as_system(monkeypatch, "mac")
    system.open_folder(tmp_path / "new")
    assert (tmp_path / "new").is_dir() and calls[-1] == ["open", str(tmp_path / "new")]
    as_system(monkeypatch, "linux")
    system.open_folder(tmp_path)
    assert calls[-1][0] == "xdg-open"
    as_system(monkeypatch, "windows")
    monkeypatch.setattr(system.os, "startfile", lambda p: calls.append(["startfile", str(p)]), raising=False)
    system.open_folder(tmp_path)
    assert calls[-1] == ["startfile", str(tmp_path)]


def test_claude_env_paths(monkeypatch, store):
    from arete import assistant
    monkeypatch.setenv("PATH", "/usr/bin")
    as_system(monkeypatch, "mac")
    env = assistant._env("/somewhere/claude")
    first, second = env["PATH"].split(os.pathsep)[:2]
    assert first == str(Path("/somewhere/claude").parent) and second == "/opt/homebrew/bin"
    assert "USERPROFILE" not in env
    as_system(monkeypatch, "windows")
    env = assistant._env(str(Path("C:/Users/x/.local/bin/claude.exe")))
    assert "/opt/homebrew/bin" not in env["PATH"] and env["USERPROFILE"]
