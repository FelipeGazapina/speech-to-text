"""Microphone capture into a 16 kHz mono float32 buffer (what Whisper expects)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from .transcriber import SAMPLE_RATE
from .watchdog import Timeout, run_with_timeout

log = logging.getLogger(__name__)

BLOCK_SECONDS = 0.05
# How much audio PortAudio buffers in C between the device and our reader thread. Our thread can
# fall this far behind (a transcription holding the GIL, the UI refreshing) without losing samples.
BUFFER_SECONDS = 1.0


class MicStream:
    """The microphone, read in blocks from our own thread.

    No Python runs on CoreAudio's real-time thread: PortAudio copies the device's audio into its own
    ring buffer there, and `on_audio` is called from here with 16 kHz mono float32 blocks. With a
    Python callback instead, every time another thread held the GIL for longer than one audio cycle
    CoreAudio logged an overload, and an overload landing on the instant the stream was closed left
    the close hanging inside coreaudiod and every later open timing out."""

    def __init__(self, on_audio: Callable[[np.ndarray], None], open_timeout: float = 5.0):
        self.on_audio = on_audio
        self.open_timeout = open_timeout
        self._stream = None
        self._rate = SAMPLE_RATE
        self._reader: threading.Thread | None = None
        self._stopped = threading.Event()

    def start(self) -> None:
        """Raises watchdog.Timeout if the microphone doesn't open within `open_timeout` seconds."""
        import sounddevice as sd

        def open_stream():
            try:
                stream = self._open(sd, SAMPLE_RATE)
            except sd.PortAudioError:
                # Some input devices refuse 16 kHz; record at their native rate and resample.
                native = int(sd.query_devices(kind="input")["default_samplerate"])
                log.info("Input device rejected %d Hz; recording at %d Hz", SAMPLE_RATE, native)
                stream = self._open(sd, native)
            stream.start()
            if self._stopped.is_set():  # opened after we gave up on it: don't leave the mic on
                close_stream(stream, "late microphone")
            return stream

        try:
            self._stream = run_with_timeout(open_stream, self.open_timeout, "Opening the microphone")
        except Timeout:
            self._stopped.set()  # if it opens late, the helper thread closes it
            raise
        self._reader = threading.Thread(target=self._read, name="microphone", daemon=True)
        self._reader.start()

    def _open(self, sd, rate: int):
        self._rate = rate
        return sd.InputStream(samplerate=rate, channels=1, dtype="float32", latency=BUFFER_SECONDS / 2)

    def _read(self) -> None:
        stream, block = self._stream, int(self._rate * BLOCK_SECONDS)
        overflows = 0
        try:
            while not self._stopped.is_set():
                data, overflowed = stream.read(block)
                overflows += overflowed
                self._deliver(data)
            pending = stream.read_available  # the end of the recording, buffered but not read yet
            if pending:
                self._deliver(stream.read(pending)[0])
        except Exception:
            if not self._stopped.is_set():
                log.exception("Reading the microphone failed")
        if overflows:
            log.warning("The microphone reader fell behind %d times; some audio was lost", overflows)

    def _deliver(self, data: np.ndarray) -> None:
        mono = data[:, 0]
        self.on_audio(mono if self._rate == SAMPLE_RATE else resample(mono, self._rate, SAMPLE_RATE))

    def stop(self) -> None:
        """Waits only for the block being read right now, so the audio up to this instant is delivered
        before this returns; then the stream is shut down on a helper thread, so a stuck audio device
        can't block the caller."""
        self._stopped.set()
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.join(timeout=0.5)
        stream, self._stream = self._stream, None
        if stream is not None:
            threading.Thread(target=close_stream, args=(stream, "microphone"), daemon=True).start()


class Recorder:
    """Starting has a deadline and stopping never waits on the audio system, so a misbehaving audio
    device (common with Bluetooth headsets switching modes) can't freeze the app."""

    OPEN_TIMEOUT = 5.0

    def __init__(self):
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._mic: MicStream | None = None
        self._session = 0  # audio arriving from an older, abandoned stream is ignored
        self.level = 0.0  # loudness of the latest audio (RMS, 0..1), for the menu bar's live meter

    @property
    def is_recording(self) -> bool:
        return self._mic is not None

    def start(self) -> None:
        """Raises watchdog.Timeout if the microphone doesn't open within OPEN_TIMEOUT seconds."""
        with self._lock:
            self._chunks = []
            self._session += 1
            session = self._session
        self.level = 0.0
        mic = MicStream(lambda samples: self._on_audio(session, samples), self.OPEN_TIMEOUT)
        mic.start()
        self._mic = mic

    def _on_audio(self, session: int, samples: np.ndarray) -> None:
        with self._lock:
            if session == self._session:
                self._chunks.append(samples)
                self.level = float(np.sqrt(np.mean(np.square(samples))))

    def snapshot(self) -> np.ndarray:
        """The audio recorded so far, without stopping. Never blocks for long."""
        if not self._lock.acquire(timeout=1):
            return np.zeros(0, dtype=np.float32)
        try:
            return np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
        finally:
            self._lock.release()

    def stop(self) -> np.ndarray:
        """Stop recording and return the audio at 16 kHz, within half a second."""
        mic, self._mic = self._mic, None
        if mic is not None:
            mic.stop()
        with self._lock:
            self._session += 1  # stop accepting audio right now
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
            self._chunks = []
        return audio


def close_stream(stream, name: str) -> None:
    """abort() drops pending buffers instead of waiting for them, which is faster and less prone to
    hanging than stop(). If it hangs anyway, only this helper thread is stuck."""
    started = time.monotonic()
    finished = threading.Event()
    # A close that never returns leaves no trace otherwise, and could explain the next open failing.
    stuck = threading.Timer(5, lambda: finished.is_set() or log.error("Closing the %s stream is still hanging after 5s", name))
    stuck.daemon = True
    stuck.start()
    try:
        stream.abort(ignore_errors=True)
        stream.close(ignore_errors=True)
    except Exception:
        log.exception("Closing the %s stream failed", name)
    finally:
        finished.set()
        stuck.cancel()
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
