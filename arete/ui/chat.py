"""
The Claude chat bar, pinned to the bottom of every page.

Flow for one message:
  1. The bar sends a "send" trigger (or "mic" start/stop). The callback records
     the message and sets chat_busy = "thinking" (or "transcribing").
  2. render() draws the bar first, so "Thinking..." shows, then does the slow
     work (transcribe, then ask Claude) and reruns, so every page, including
     the timetable grid, shows any changes straight away.
  3. Deletes/overwrites come back as previews; Confirm / Cancel in the panel
     sends a "confirm" trigger that runs or drops them.
"""

import uuid
from pathlib import Path

import streamlit as st

from arete import assistant
from arete import tools
from arete import voice

HERE = Path(__file__).parent

_chat_component = st.components.v2.component(
    "arete_chat",
    html='<div class="cb-root"></div>',
    css=(HERE / "components" / "chat_component.css").read_text(encoding="utf-8"),
    js=(HERE / "components" / "chat_component.js").read_text(encoding="utf-8"),
)

MAX_SHOWN = 60   # messages sent to the panel


def _init():
    sess = st.session_state
    sess.setdefault("chat_messages", [])     # [{role: user|assistant|notice|error, text}]
    sess.setdefault("chat_pending", {})      # id -> tools.Pending
    sess.setdefault("chat_busy", None)       # None | "thinking" | "transcribing"
    sess.setdefault("chat_queue", None)      # message waiting for Claude
    sess.setdefault("chat_audio", None)      # recording waiting to be transcribed
    sess.setdefault("chat_notes", [])        # confirm/cancel results to tell Claude next time
    sess.setdefault("chat_error", None)      # shown above the bar
    sess.setdefault("chat_recording", False)


def _say(role, text):
    st.session_state["chat_messages"].append({"role": role, "text": text})


# ---------------------------------------------------------------------------
# Component callbacks (run before the page, at the start of the rerun)
# ---------------------------------------------------------------------------

def _on_send():
    value = st.session_state["chat"]["send"]
    text = (value or {}).get("text", "").strip()
    if not text or st.session_state["chat_busy"]:
        return
    _say("user", text)
    st.session_state["chat_queue"] = text
    st.session_state["chat_busy"] = "thinking"
    st.session_state["chat_error"] = None


def _on_mic():
    action = (st.session_state["chat"]["mic"] or {}).get("action")
    sess = st.session_state
    sess["chat_error"] = None
    try:
        if action == "start":
            voice.recorder.start()
            sess["chat_recording"] = True
        elif action == "stop":
            sess["chat_recording"] = False
            sess["chat_audio"] = voice.recorder.stop()
            sess["chat_busy"] = "transcribing"
    except voice.VoiceError as e:
        sess["chat_recording"] = voice.recorder.recording
        sess["chat_error"] = str(e)


def _on_confirm():
    value = st.session_state["chat"]["confirm"] or {}
    pending = st.session_state["chat_pending"].pop(value.get("id"), None)
    if not pending:
        return
    if not value.get("ok"):
        _say("notice", "Cancelled.")
        st.session_state["chat_notes"].append(f"The user cancelled: {pending.summary}.")
        return
    try:
        result = pending.run()
        _say("notice", f"✓ {result.text}")
        st.session_state["chat_notes"].append(f"The user confirmed, and it was done: {pending.summary}.")
    except tools.ToolError as e:
        _say("error", f"Couldn't do that: {e}")


# ---------------------------------------------------------------------------
# The slow work, done after the bar is drawn
# ---------------------------------------------------------------------------

def _transcribe():
    sess = st.session_state
    try:
        text = voice.transcribe(sess["chat_audio"])
        _say("user", text)                  # show the transcript as the user's message
        sess["chat_queue"] = text
        sess["chat_busy"] = "thinking"
    except voice.VoiceError as e:
        sess["chat_error"] = str(e)
        sess["chat_busy"] = None
    finally:
        sess["chat_audio"] = None


def _ask_claude():
    sess = st.session_state
    prompt = sess["chat_queue"]
    if sess["chat_notes"]:
        # Let Claude know what happened to its previews since the last reply.
        prompt = "(Since your last reply: " + " ".join(sess["chat_notes"]) + ")\n\n" + prompt
    # Earlier messages go along as context (the chat is only kept in memory, never on disk).
    history = [(m["role"], m["text"]) for m in sess["chat_messages"]]
    if history and history[-1] == ("user", sess["chat_queue"]):
        history = history[:-1]                 # that's the message being sent now
    turn = assistant.run_turn(prompt, history)
    if turn.error:
        _say("error", turn.error)
    else:
        sess["chat_notes"] = []
        if turn.reply:
            _say("assistant", turn.reply)
        for p in turn.pending:
            sess["chat_pending"][uuid.uuid4().hex] = p
    sess["chat_queue"] = None
    sess["chat_busy"] = None


def render(inline=False):
    """
    inline=False: the bar pinned to the bottom of the page (every page but Home).
    inline=True:  a full-height chat panel in place, e.g. the Home page's right column.
    """
    _init()
    sess = st.session_state

    if not inline:
        # Leave room under the page so the bar never covers content.
        st.html("<style>.stMainBlockContainer { padding-bottom: 8rem; }</style>")

    data = {
        "inline": inline,
        "messages": sess["chat_messages"][-MAX_SHOWN:],
        "pending": [{"id": pid, "summary": p.summary} for pid, p in sess["chat_pending"].items()],
        "busy": sess["chat_busy"],
        "recording": sess["chat_recording"],
        "error": sess["chat_error"],
    }
    _chat_component(
        data=data, key="chat",
        on_send_change=_on_send, on_mic_change=_on_mic, on_confirm_change=_on_confirm,
    )

    if sess["chat_busy"] == "transcribing":
        _transcribe()
        st.rerun()              # show the transcript, then ask Claude
    elif sess["chat_busy"] == "thinking":
        _ask_claude()
        st.rerun()              # show the reply and any changes on the page
