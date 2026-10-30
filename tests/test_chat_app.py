"""
The app as a whole (AppTest), with Claude replaced by a fake so no real
model calls happen. Uses the throwaway database from the `store` fixture.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def fake_claude(store, monkeypatch):
    from arete import assistant
    from arete import tools

    calls = []

    def run_turn(text, history=()):
        calls.append((text, list(history)))
        if "move" in text:
            result = tools.add_block("2026-10-29", "plan", "08:00", "09:00", "Gym")
            return assistant.Turn(reply="Confirm to overwrite Sleep.",
                                  pending=[result.pending])
        tools.add_block("2026-10-29", "plan", "07:00", "09:00", "Sleep")
        return assistant.Turn(reply="Added Sleep 07:00–09:00.", changed=True)

    monkeypatch.setattr(assistant, "run_turn", run_turn)
    return calls


def test_every_page_renders_with_chat_bar(store):
    at = AppTest.from_file(APP, default_timeout=60).run()
    for page in at.sidebar.radio[0].options:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, (page, at.exception)
        assert at.session_state["chat_busy"] is None


def test_message_runs_claude_and_saves(store, fake_claude):
    at = AppTest.from_file(APP, default_timeout=60).run()
    # What the "send" callback does when the user presses Enter:
    at.session_state["chat_messages"] = [{"role": "user", "text": "slept 7 to 9"}]
    at.session_state["chat_queue"] = "slept 7 to 9"
    at.session_state["chat_busy"] = "thinking"
    at.run()
    assert not at.exception
    assert fake_claude == [("slept 7 to 9", [])]
    assert at.session_state["chat_messages"][-1] == {"role": "assistant", "text": "Added Sleep 07:00–09:00."}
    assert at.session_state["chat_busy"] is None
    assert [(b["start"], b["label"]) for b in store.blocks_between("2026-10-29", "2026-10-29")] == [("07:00", "Sleep")]


def test_preview_waits_for_confirm(store, fake_claude):
    at = AppTest.from_file(APP, default_timeout=60).run()
    for text in ("slept 7 to 9", "move gym over it"):
        at.session_state["chat_queue"] = text
        at.session_state["chat_busy"] = "thinking"
        at.run()
    assert ("assistant", "Added Sleep 07:00–09:00.") in fake_claude[1][1]   # earlier messages go along as context
    pending = at.session_state["chat_pending"]
    assert len(pending) == 1
    assert [b["label"] for b in store.blocks_between("2026-10-29", "2026-10-29")] == ["Sleep"]
    next(iter(pending.values())).run()                     # what Confirm does
    assert [(b["start"], b["end"], b["label"]) for b in store.blocks_between("2026-10-29", "2026-10-29")] == [
        ("07:00", "08:00", "Sleep"), ("08:00", "09:00", "Gym")]


def test_claude_error_is_shown_not_raised(store, monkeypatch):
    from arete import assistant
    monkeypatch.setattr(assistant, "run_turn", lambda text, history=(): assistant.Turn(error=assistant.LOGIN_HELP))
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state["chat_queue"] = "hi"
    at.session_state["chat_busy"] = "thinking"
    at.run()
    assert not at.exception
    assert at.session_state["chat_messages"][-1] == {"role": "error", "text": assistant.LOGIN_HELP}


def test_voice_transcript_becomes_message(store, fake_claude, monkeypatch):
    import numpy as np
    from arete import voice
    monkeypatch.setattr(voice, "transcribe", lambda audio: "slept 7 to 9")
    at = AppTest.from_file(APP, default_timeout=60).run()
    # What the mic "stop" callback leaves behind:
    at.session_state["chat_audio"] = np.zeros(16000, np.float32)
    at.session_state["chat_busy"] = "transcribing"
    at.run()
    assert not at.exception
    roles = [(m["role"], m["text"]) for m in at.session_state["chat_messages"]]
    assert roles == [("user", "slept 7 to 9"), ("assistant", "Added Sleep 07:00–09:00.")]


def test_voice_error_is_shown(store, monkeypatch):
    import numpy as np
    from arete import voice

    def fail(audio):
        raise voice.VoiceError("Couldn't make out any words.")

    monkeypatch.setattr(voice, "transcribe", fail)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state["chat_audio"] = np.zeros(16000, np.float32)
    at.session_state["chat_busy"] = "transcribing"
    at.run()
    assert not at.exception
    assert at.session_state["chat_error"] == "Couldn't make out any words."
    assert at.session_state["chat_busy"] is None
