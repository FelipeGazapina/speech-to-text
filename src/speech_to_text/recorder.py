"""Microphone capture into a 16 kHz mono float32 buffer (what Whisper expects)."""

from __future__ import annotations

import logging
import threading

import numpy as np

from .transcriber import SAMPLE_RATE

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self):
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None
        self._rate = SAMPLE_RATE

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        import sounddevice as sd

        with self._lock:
            self._chunks = []
        try:
            self._stream = self._open(sd, SAMPLE_RATE)
        except sd.PortAudioError:
            # Some input devices refuse 16 kHz; record at their native rate and resample.
            native = int(sd.query_devices(kind="input")["default_samplerate"])
            log.info("Input device rejected %d Hz; recording at %d Hz", SAMPLE_RATE, native)
            self._stream = self._open(sd, native)
        self._stream.start()

    def _open(self, sd, rate: int):
        self._rate = rate
        return sd.InputStream(samplerate=rate, channels=1, dtype="float32", callback=self._on_audio)

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("Audio status: %s", status)
        with self._lock:
            self._chunks.append(indata[:, 0].copy())

    def stop(self) -> np.ndarray:
        """Stop recording and return the audio at 16 kHz."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
            self._chunks = []
        return resample(audio, self._rate, SAMPLE_RATE)


def resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or len(audio) == 0:
        return audio.astype(np.float32)
    duration = len(audio) / source_rate
    target_times = np.arange(int(duration * target_rate)) / target_rate
    source_times = np.arange(len(audio)) / source_rate
    return np.interp(target_times, source_times, audio).astype(np.float32)


def is_silent(audio: np.ndarray, min_seconds: float = 0.3, min_rms: float = 0.0015) -> bool:
    """Too short or too quiet to be speech; Whisper would just hallucinate on it."""
    if len(audio) < min_seconds * SAMPLE_RATE:
        return True
    return float(np.sqrt(np.mean(np.square(audio)))) < min_rms
