"""
Voice input for the chat bar: record on the Python side with sounddevice (the
pywebview window may not allow browser microphone access), then transcribe
locally with faster-whisper. Nothing leaves the machine.

macOS asks for microphone permission the first time; Arete.app's Info.plist
has NSMicrophoneUsageDescription for that prompt.
"""

import threading

import numpy as np

SAMPLE_RATE = 16000          # what Whisper expects
MAX_SECONDS = 120            # stop keeping audio after this
MODEL_NAME = "small.en"      # downloaded once (~470 MB) into ~/.cache/huggingface


class VoiceError(Exception):
    """A problem to show in the chat bar (no mic, permission, nothing heard...)."""


class Recorder:
    """Records from the default input device between start() and stop()."""

    def __init__(self):
        self._stream = None
        self._chunks = []
        self._frames = 0
        self._rate = SAMPLE_RATE
        self._lock = threading.Lock()

    @property
    def recording(self):
        return self._stream is not None

    def start(self):
        if self.recording:
            return
        try:
            import sounddevice as sd
            device = sd.query_devices(kind="input")    # raises if there's no input device
            # Record at the mic's own rate (often 48 kHz) and resample in stop().
            self._rate = int(device["default_samplerate"])
            self._chunks, self._frames = [], 0
            self._stream = sd.InputStream(samplerate=self._rate, channels=1, dtype="float32",
                                          callback=self._on_audio)
            self._stream.start()
        except Exception as e:
            self._stream = None
            raise VoiceError(f"Couldn't start the microphone ({e}).") from e

    def _on_audio(self, indata, frames, time, status):
        with self._lock:
            if self._frames < MAX_SECONDS * self._rate:
                self._chunks.append(indata[:, 0].copy())
                self._frames += frames

    def stop(self):
        """Stop and return the audio as float32 samples at 16 kHz."""
        if not self.recording:
            raise VoiceError("The microphone wasn't recording.")
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
        audio = resample(audio, self._rate)
        if audio.size < SAMPLE_RATE * 0.4:
            raise VoiceError("That recording was too short. Click the mic, speak, then click it again.")
        # A denied microphone permission records pure silence rather than failing.
        if np.abs(audio).max() < 1e-4:
            raise VoiceError("Didn't hear anything. Check that Arete may use the microphone in "
                             "System Settings › Privacy & Security › Microphone.")
        return audio


def resample(audio, rate):
    """Linear resample to 16 kHz (plenty for speech)."""
    if rate == SAMPLE_RATE or audio.size == 0:
        return audio.astype(np.float32)
    n = int(round(audio.size * SAMPLE_RATE / rate))
    return np.interp(np.linspace(0, audio.size - 1, n), np.arange(audio.size), audio).astype(np.float32)


# One recorder for the app process (Arete is single-user).
recorder = Recorder()

_model = None
_model_lock = threading.Lock()


def get_model():
    """Load the Whisper model once and keep it."""
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
        return _model


def transcribe(audio):
    """Speech -> text. Raises VoiceError if nothing was understood."""
    try:
        segments, _ = get_model().transcribe(audio, language="en", beam_size=1, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
    except Exception as e:
        raise VoiceError(f"Couldn't transcribe the recording ({e}).") from e
    if not text:
        raise VoiceError("Couldn't make out any words. Try again a little closer to the mic.")
    return text
