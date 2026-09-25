"""Microphone capture into a 16 kHz mono float32 buffer (what Whisper expects)."""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from .transcriber import SAMPLE_RATE
from .watchdog import Timeout, run_with_timeout

log = logging.getLogger(__name__)


class Recorder:
    """Starting has a deadline and stopping never waits on the audio system, so a misbehaving audio
    device (common with Bluetooth headsets switching modes) can't freeze the app."""

    OPEN_TIMEOUT = 5.0

    def __init__(self):
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None
        self._rate = SAMPLE_RATE
        self._session = 0  # audio arriving from an older, abandoned stream is ignored

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        """Raises watchdog.Timeout if the microphone doesn't open within OPEN_TIMEOUT seconds."""
        import sounddevice as sd

        with self._lock:
            self._chunks = []
            self._session += 1
            session = self._session

        def open_stream():
            try:
                stream = self._open(sd, SAMPLE_RATE, session)
            except sd.PortAudioError:
                # Some input devices refuse 16 kHz; record at their native rate and resample.
                native = int(sd.query_devices(kind="input")["default_samplerate"])
                log.info("Input device rejected %d Hz; recording at %d Hz", SAMPLE_RATE, native)
                stream = self._open(sd, native, session)
            stream.start()
            if session != self._session:  # opened after we gave up on it: don't leave the mic on
                close_stream(stream, "late microphone")
            return stream

        try:
            self._stream = run_with_timeout(open_stream, self.OPEN_TIMEOUT, "Opening the microphone")
        except Timeout:
            with self._lock:
                self._session += 1  # if it opens late, its audio goes nowhere
            raise

    def _open(self, sd, rate: int, session: int):
        self._rate = rate

        def on_audio(indata, frames, time_info, status) -> None:
            if status:
                log.debug("Audio status: %s", status)
            with self._lock:
                if session == self._session:
                    self._chunks.append(indata[:, 0].copy())

        return sd.InputStream(samplerate=rate, channels=1, dtype="float32", callback=on_audio)

    def snapshot(self) -> np.ndarray:
        """The audio recorded so far, at 16 kHz, without stopping. Never blocks for long."""
        if not self._lock.acquire(timeout=1):
            return np.zeros(0, dtype=np.float32)
        try:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
        finally:
            self._lock.release()
        return resample(audio, self._rate, SAMPLE_RATE)

    def stop(self) -> np.ndarray:
        """Stop recording and return the audio at 16 kHz. Returns immediately: the audio stream is
        shut down on a helper thread, so a stuck audio device can't block the caller."""
        stream, self._stream = self._stream, None
        with self._lock:
            self._session += 1  # stop accepting audio right now
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
            self._chunks = []
        if stream is not None:
            threading.Thread(target=close_stream, args=(stream, "microphone"), daemon=True).start()
        return resample(audio, self._rate, SAMPLE_RATE)


def close_stream(stream, name: str) -> None:
    """abort() drops pending buffers instead of waiting for them, which is faster and less prone to
    hanging than stop(). If it hangs anyway, only this helper thread is stuck."""
    started = time.monotonic()
    try:
        stream.abort(ignore_errors=True)
        stream.close(ignore_errors=True)
    except Exception:
        log.exception("Closing the %s stream failed", name)
    took = time.monotonic() - started
    if took > 2:
        log.warning("Closing the %s stream took %.1fs", name, took)


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
