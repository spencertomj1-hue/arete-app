"""Voice input. The recorder uses a fake sounddevice, so no real microphone is used."""

import shutil
import subprocess
import sys
import types
import wave

import numpy as np
import pytest

import voice


class FakeStream:
    """Stands in for sounddevice.InputStream; feeds `audio` to the callback on start()."""
    audio = np.zeros(0, np.float32)

    def __init__(self, samplerate, channels, dtype, callback):
        self.callback = callback

    def start(self):
        for chunk in np.array_split(FakeStream.audio, 10):
            self.callback(chunk.reshape(-1, 1), len(chunk), None, None)

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_sd(monkeypatch):
    sd = types.SimpleNamespace(
        query_devices=lambda kind: {"name": "Fake mic", "default_samplerate": 48000.0},
        InputStream=FakeStream,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    return sd


def test_records_and_resamples_to_16k(fake_sd):
    FakeStream.audio = (0.1 * np.sin(np.linspace(0, 2000, 48000))).astype(np.float32)   # 1 s at 48 kHz
    r = voice.Recorder()
    r.start()
    assert r.recording
    audio = r.stop()
    assert not r.recording
    assert audio.dtype == np.float32 and abs(audio.size - 16000) <= 1


def test_silence_means_permission_problem(fake_sd):
    FakeStream.audio = np.zeros(48000, np.float32)
    r = voice.Recorder()
    r.start()
    with pytest.raises(voice.VoiceError, match="Privacy"):
        r.stop()


def test_too_short(fake_sd):
    FakeStream.audio = np.full(4800, 0.1, np.float32)     # 0.1 s
    r = voice.Recorder()
    r.start()
    with pytest.raises(voice.VoiceError, match="too short"):
        r.stop()


def test_no_microphone(monkeypatch):
    def no_device(kind):
        raise RuntimeError("No input device")
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(query_devices=no_device))
    with pytest.raises(voice.VoiceError, match="Couldn't start the microphone"):
        voice.Recorder().start()


@pytest.mark.skipif(not shutil.which("say"), reason="needs macOS `say` to make test speech")
def test_transcribes_speech_at_48k(tmp_path):
    aiff, wav = tmp_path / "s.aiff", tmp_path / "s.wav"
    subprocess.run(["say", "-o", str(aiff), "Add a meeting from nine to eleven tomorrow."], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@48000", "-c", "1", str(aiff), str(wav)], check=True)
    with wave.open(str(wav)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    text = voice.transcribe(voice.resample(audio, 48000)).lower()
    assert "meeting" in text and "tomorrow" in text
