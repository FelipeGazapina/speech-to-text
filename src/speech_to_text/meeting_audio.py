"""Recording a meeting: your microphone and the computer's audio (the other people), as two tracks.

Each track streams to a raw 16 kHz mono int16 file (.pcm) on disk instead of memory, so a
2-hour meeting costs no RAM, and if the app quits mid-meeting the audio so far is still usable
(a raw file has no header that could be left half-written). The files are temporary: they're
deleted as soon as the meeting is transcribed.

The computer's audio comes from ScreenCaptureKit, macOS's own capture API, which needs the
"Screen & System Audio Recording" permission. Nothing of the screen is kept: the video it
insists on delivering is 2x2 pixels, once a second, and thrown away.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from pathlib import Path

import numpy as np

from .recorder import close_stream, resample
from .transcriber import SAMPLE_RATE
from .watchdog import Timeout, run_with_timeout

log = logging.getLogger(__name__)


class PcmWriter:
    """Appends audio to a raw PCM file from a background thread, keeping the track in step with the
    wall clock: if a source goes quiet (no buffers at all), silence is filled in, so both tracks'
    timestamps line up."""

    def __init__(self, path: Path, started: float):
        self.path = path
        self.started = started
        self.frames = 0
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._lock = threading.Lock()
        self._file = open(path, "ab")
        self._thread = threading.Thread(target=self._drain, name=f"pcm-{path.stem}", daemon=True)
        self._thread.start()

    def write(self, samples: np.ndarray) -> None:
        with self._lock:
            behind = int((time.monotonic() - self.started) * SAMPLE_RATE) - len(samples) - self.frames
            if behind > SAMPLE_RATE // 2:  # more than half a second of missing audio: fill the gap
                self._queue.put(np.zeros(behind, dtype=np.float32))
                self.frames += behind
            self._queue.put(np.asarray(samples, dtype=np.float32).copy())
            self.frames += len(samples)

    def close(self, total_seconds: float | None = None) -> None:
        with self._lock:
            if total_seconds is not None:
                missing = int(total_seconds * SAMPLE_RATE) - self.frames
                if missing > 0:
                    self._queue.put(np.zeros(missing, dtype=np.float32))
                    self.frames += missing
            self._queue.put(None)
        self._thread.join(timeout=10)
        self._file.close()

    def _drain(self) -> None:
        while (chunk := self._queue.get()) is not None:
            self._file.write((np.clip(chunk, -1.0, 1.0) * 32767).astype("<i2").tobytes())
            self._file.flush()


class MicCapture:
    def __init__(self, writer: PcmWriter):
        self.writer = writer
        self._stream = None
        self._rate = SAMPLE_RATE

    def start(self) -> None:
        import sounddevice as sd

        def on_audio(indata, frames, time_info, status) -> None:
            mono = indata[:, 0]
            self.writer.write(mono if self._rate == SAMPLE_RATE else resample(mono, self._rate, SAMPLE_RATE))

        def open_stream():
            try:
                stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=on_audio)
            except sd.PortAudioError:
                self._rate = int(sd.query_devices(kind="input")["default_samplerate"])
                stream = sd.InputStream(samplerate=self._rate, channels=1, dtype="float32", callback=on_audio)
            stream.start()
            return stream

        self._stream = run_with_timeout(open_stream, 5, "Opening the microphone")

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            threading.Thread(target=close_stream, args=(stream, "meeting microphone"), daemon=True).start()


# --- the computer's audio, through ScreenCaptureKit ---


class _AudioStreamBasicDescription(ctypes.Structure):
    _fields_ = [
        ("mSampleRate", ctypes.c_double),
        ("mFormatID", ctypes.c_uint32),
        ("mFormatFlags", ctypes.c_uint32),
        ("mBytesPerPacket", ctypes.c_uint32),
        ("mFramesPerPacket", ctypes.c_uint32),
        ("mBytesPerFrame", ctypes.c_uint32),
        ("mChannelsPerFrame", ctypes.c_uint32),
        ("mBitsPerChannel", ctypes.c_uint32),
        ("mReserved", ctypes.c_uint32),
    ]


_FLAG_FLOAT, _FLAG_NON_INTERLEAVED = 1, 1 << 5
_coremedia = None
_output_class = None


def _core_media():
    """CoreMedia through ctypes: plain C calls to read the raw samples out of a CMSampleBuffer."""
    global _coremedia
    if _coremedia is None:
        cm = ctypes.CDLL("/System/Library/Frameworks/CoreMedia.framework/CoreMedia")
        for name, restype, argtypes in [
            ("CMSampleBufferGetDataBuffer", ctypes.c_void_p, [ctypes.c_void_p]),
            ("CMSampleBufferGetNumSamples", ctypes.c_long, [ctypes.c_void_p]),
            ("CMSampleBufferGetFormatDescription", ctypes.c_void_p, [ctypes.c_void_p]),
            ("CMAudioFormatDescriptionGetStreamBasicDescription",
             ctypes.POINTER(_AudioStreamBasicDescription), [ctypes.c_void_p]),
            ("CMBlockBufferGetDataLength", ctypes.c_size_t, [ctypes.c_void_p]),
            ("CMBlockBufferCopyDataBytes", ctypes.c_int32,
             [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p]),
        ]:
            function = getattr(cm, name)
            function.restype, function.argtypes = restype, argtypes
        _coremedia = cm
    return _coremedia


def _pointer(objc_object) -> int:
    import objc

    pointer = getattr(objc_object, "__pointer__", None)
    return pointer if isinstance(pointer, int) else objc.pyobjc_id(objc_object)


def samples_from_buffer(sample_buffer) -> np.ndarray:
    """Mono float32 samples at 16 kHz from an audio CMSampleBuffer, whatever its exact format."""
    cm = _core_media()
    buffer = _pointer(sample_buffer)
    frames = cm.CMSampleBufferGetNumSamples(buffer)
    block = cm.CMSampleBufferGetDataBuffer(buffer)
    if not block or frames <= 0:
        return np.zeros(0, dtype=np.float32)
    description = cm.CMAudioFormatDescriptionGetStreamBasicDescription(cm.CMSampleBufferGetFormatDescription(buffer))
    format_ = description.contents if description else None
    length = cm.CMBlockBufferGetDataLength(block)
    raw = (ctypes.c_char * length)()
    if cm.CMBlockBufferCopyDataBytes(block, 0, length, raw) != 0:
        return np.zeros(0, dtype=np.float32)

    channels = max(1, format_.mChannelsPerFrame) if format_ else 1
    rate = int(format_.mSampleRate) if format_ else SAMPLE_RATE
    is_float = bool(format_.mFormatFlags & _FLAG_FLOAT) if format_ else True
    data = np.frombuffer(raw, dtype=np.float32 if is_float else "<i2").astype(np.float32)
    if not is_float:
        data /= 32767
    if channels > 1:
        if format_ and format_.mFormatFlags & _FLAG_NON_INTERLEAVED:
            data = data[: frames * channels].reshape(channels, frames).mean(axis=0)
        else:
            data = data[: frames * channels].reshape(frames, channels).mean(axis=1)
    return resample(data[:frames], rate, SAMPLE_RATE) if rate != SAMPLE_RATE else data[:frames]


def _stream_output_class():
    global _output_class
    if _output_class is None:
        import importlib

        import objc
        from Foundation import NSObject

        importlib.import_module("ScreenCaptureKit")  # registers the SCStreamOutput protocol

        class SpeechToTextStreamOutput(NSObject, protocols=[objc.protocolNamed("SCStreamOutput")]):
            def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
                if output_type == 1 and self.on_audio is not None:  # SCStreamOutputTypeAudio
                    try:
                        self.on_audio(sample_buffer)
                    except Exception:
                        log.exception("Couldn't read a system audio buffer")

        _output_class = SpeechToTextStreamOutput
    return _output_class


def system_audio_permission() -> bool:
    try:
        import Quartz

        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return False


def request_system_audio_permission() -> None:
    try:
        import Quartz

        Quartz.CGRequestScreenCaptureAccess()
    except Exception:
        log.exception("Screen & System Audio Recording request failed")


class SystemAudioCapture:
    def __init__(self, writer: PcmWriter):
        self.writer = writer
        self._stream = None
        self._output = None

    def start(self) -> None:
        """Raises PermissionError without the Screen & System Audio Recording permission."""
        import CoreMedia
        import ScreenCaptureKit as SC

        if not system_audio_permission():
            request_system_audio_permission()
            raise PermissionError("Screen & System Audio Recording isn't allowed")

        content = _wait(lambda done: SC.SCShareableContent
                        .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                            False, True, lambda content, error: done(content, error)))
        displays = content.displays() if content else []
        if not displays:
            raise RuntimeError("No display to attach the audio capture to")

        config = SC.SCStreamConfiguration.alloc().init()
        config.setCapturesAudio_(True)
        config.setExcludesCurrentProcessAudio_(True)  # not our own start/stop sounds
        config.setSampleRate_(SAMPLE_RATE)
        config.setChannelCount_(1)
        config.setWidth_(2)
        config.setHeight_(2)
        config.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))

        content_filter = SC.SCContentFilter.alloc().initWithDisplay_excludingWindows_(displays[0], [])
        self._stream = SC.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, config, None)
        self._output = _stream_output_class().alloc().init()
        self._output.on_audio = lambda buffer: self.writer.write(samples_from_buffer(buffer))
        for output_type in (SC.SCStreamOutputTypeScreen, SC.SCStreamOutputTypeAudio):
            ok, error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
                self._output, output_type, None, None
            )
            if not ok:
                raise RuntimeError(f"Couldn't set up the audio capture: {error}")
        _wait(lambda done: self._stream.startCaptureWithCompletionHandler_(lambda error: done(True, error)))

    def stop(self) -> None:
        if self._stream is not None:
            try:
                _wait(lambda done: self._stream.stopCaptureWithCompletionHandler_(lambda error: done(True, error)))
            except Exception:
                log.exception("Stopping the system audio capture failed")
            self._stream = None
        if self._output is not None:
            self._output.on_audio = None


def _wait(start, timeout: float = 15):
    """Run an Objective-C call that reports back through a completion handler, and wait for it."""
    finished = threading.Event()
    outcome: dict = {}

    def done(value, error) -> None:
        outcome["value"], outcome["error"] = value, error
        finished.set()

    start(done)
    if not finished.wait(timeout):
        raise TimeoutError("macOS didn't answer in time")
    if outcome.get("error") is not None:
        raise RuntimeError(str(outcome["error"]))
    return outcome.get("value")


class MeetingRecorder:
    def __init__(self):
        self.started = 0.0
        self.folder: Path | None = None
        self.system_audio = False
        self._writers: list[PcmWriter] = []
        self._captures: list = []

    @property
    def is_recording(self) -> bool:
        return self.folder is not None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started if self.is_recording else 0.0

    def start(self, folder: Path) -> bool:
        """Start both tracks. Returns whether the computer's audio is being recorded too."""
        folder.mkdir(parents=True, exist_ok=True)
        self.started = time.monotonic()
        mic_writer = PcmWriter(folder / "me.pcm", self.started)
        mic = MicCapture(mic_writer)
        mic.start()
        self._writers, self._captures = [mic_writer], [mic]

        system_writer = PcmWriter(folder / "others.pcm", self.started)
        system = SystemAudioCapture(system_writer)
        try:
            system.start()
            self._writers.append(system_writer)
            self._captures.append(system)
            self.system_audio = True
        except Exception as exc:
            log.warning("Recording the meeting without the computer's audio: %s", exc)
            system_writer.close()
            system_writer.path.unlink(missing_ok=True)
            self.system_audio = False
        self.folder = folder
        return self.system_audio

    def stop(self) -> float:
        """Stop recording; returns the duration in seconds. The files stay for transcription.
        Each track gets a deadline to stop, so a stuck audio device can't hang the app."""
        duration = self.elapsed
        for capture in self._captures:
            try:
                run_with_timeout(capture.stop, 20, f"Stopping {type(capture).__name__}")
            except Timeout:
                pass  # already logged; the audio written so far is kept
            except Exception:
                log.exception("Stopping a meeting track failed")
        for writer in self._writers:
            writer.close(total_seconds=duration)
        self._writers, self._captures, self.folder = [], [], None
        return duration
