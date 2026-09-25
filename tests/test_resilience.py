"""Nothing that can hang may freeze the app: deadlines, a watchdog, and saving what was recorded."""

import json
import sys
import threading
import time
import types

import numpy as np
import pytest

from speech_to_text import watchdog as wd
from speech_to_text.config import TranscriptionConfig
from speech_to_text.recorder import Recorder
from speech_to_text.transcriber import SAMPLE_RATE, Transcriber, TranscriptionTimeout


def test_run_with_timeout():
    assert wd.run_with_timeout(lambda: 42, 1, "quick") == 42
    with pytest.raises(ValueError):
        wd.run_with_timeout(lambda: (_ for _ in ()).throw(ValueError("boom")), 1, "failing")
    started = time.monotonic()
    with pytest.raises(wd.Timeout):
        wd.run_with_timeout(lambda: time.sleep(5), 0.2, "stuck")
    assert time.monotonic() - started < 1


def test_watchdog_fires_once_when_the_main_thread_stops_beating():
    frozen = []
    dog = wd.MainThreadWatchdog(frozen.append, limit=0.3, check_every=0.05).start()
    for _ in range(10):  # a healthy main thread
        dog.beat()
        time.sleep(0.05)
    assert frozen == []
    time.sleep(0.8)  # ... then it hangs
    assert len(frozen) == 1 and frozen[0] > 0.3
    dog.stop()


# --- the microphone ---


class FakeStream:
    def __init__(self, callback, hang_on_close=False):
        self.callback, self.hang_on_close, self.closed = callback, hang_on_close, False

    def start(self):
        self.callback(np.full((1600, 1), 0.3, dtype=np.float32), 1600, None, None)

    def abort(self, ignore_errors=True):
        if self.hang_on_close:
            time.sleep(60)

    def close(self, ignore_errors=True):
        self.closed = True


@pytest.fixture
def fake_sounddevice(monkeypatch):
    module = types.ModuleType("sounddevice")
    module.PortAudioError = type("PortAudioError", (Exception,), {})
    module.behaviour = {"open_delay": 0.0, "hang_on_close": False}
    module.streams = []

    def input_stream(samplerate, channels, dtype, callback):
        time.sleep(module.behaviour["open_delay"])
        stream = FakeStream(callback, module.behaviour["hang_on_close"])
        module.streams.append(stream)
        return stream

    module.InputStream = input_stream
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module


def test_stopping_never_waits_for_a_stuck_audio_device(fake_sounddevice):
    fake_sounddevice.behaviour["hang_on_close"] = True
    recorder = Recorder()
    recorder.start()
    started = time.monotonic()
    audio = recorder.stop()
    assert time.monotonic() - started < 0.5  # the old code waited here forever
    assert len(audio) == 1600 and not recorder.is_recording
    fake_sounddevice.behaviour["hang_on_close"] = False
    recorder.start()  # and the next recording still works
    assert recorder.is_recording and len(recorder.stop()) == 1600


def test_a_microphone_that_never_opens_times_out(fake_sounddevice, monkeypatch):
    monkeypatch.setattr(Recorder, "OPEN_TIMEOUT", 0.2)
    fake_sounddevice.behaviour["open_delay"] = 0.6
    recorder = Recorder()
    with pytest.raises(wd.Timeout):
        recorder.start()
    assert not recorder.is_recording
    time.sleep(0.8)  # it opens late: it must be closed, not left recording in the background
    assert fake_sounddevice.streams and fake_sounddevice.streams[0].closed
    assert len(recorder.snapshot()) == 0


# --- Whisper ---


class HangingTranscriber(Transcriber):
    def __init__(self):
        super().__init__(TranscriptionConfig(backend="faster-whisper", model="tiny", languages=["en"]))
        self.hang = True
        self.loads = []

    def _load(self):
        self.loads.append(threading.get_ident())

    def _run(self, audio, language, prompt):
        if self.hang:
            time.sleep(30)
        return threading.get_ident()


def test_a_hung_transcription_times_out_and_the_next_one_works(monkeypatch):
    monkeypatch.setattr("speech_to_text.transcriber.transcription_deadline", lambda seconds: 0.3)
    transcriber = HangingTranscriber()
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    first_thread = transcriber._on_model_thread(threading.get_ident)

    started = time.monotonic()
    with pytest.raises(TranscriptionTimeout):
        transcriber.transcribe(audio, "en", None)
    assert time.monotonic() - started < 2 and transcriber.timeouts == 1

    transcriber.hang = False
    thread = transcriber.transcribe(audio, "en", None)
    assert thread != first_thread  # a fresh model thread...
    assert transcriber.loads == [thread]  # ...that reloaded the model before transcribing
    assert transcriber.timeouts == 0


# --- saving what was recorded, and telling the user after a restart ---


def test_pipeline_rescue_keeps_the_audio_for_a_retry(tmp_path):
    from speech_to_text.config import parse_config
    from speech_to_text.history import HistoryStore
    from speech_to_text.pipeline import Pipeline

    store = HistoryStore(tmp_path / "h.db")
    pipeline = Pipeline(parse_config(), None, None, store)
    speech = (np.sin(np.linspace(0, 4000, 32_000)) * 0.1).astype(np.float32)
    rescued = pipeline.rescue(speech, "The app froze")
    item = store.get(rescued)
    assert item.status == "failed" and "froze" in item.error and item.audio_path
    assert pipeline.rescue(np.zeros(16_000, dtype=np.float32), "silence") is None


@pytest.fixture
def app_module(monkeypatch):
    fake_rumps = types.ModuleType("rumps")
    fake_rumps.App = object
    monkeypatch.setitem(sys.modules, "rumps", fake_rumps)
    sys.modules.pop("speech_to_text.app", None)
    import speech_to_text.app as app_module

    yield app_module
    sys.modules.pop("speech_to_text.app", None)


def test_freeze_recovery_saves_the_recording_and_restarts(app_module, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    app = app_module.SpeechToTextApp.__new__(app_module.SpeechToTextApp)
    app.recorder = types.SimpleNamespace(is_recording=True, snapshot=lambda: np.ones(10, dtype=np.float32))
    rescued = []
    app.pipeline = types.SimpleNamespace(rescue=lambda audio, reason: rescued.append(len(audio)) or 7)
    app.meetings = types.SimpleNamespace(current_id=3)
    launched, exits = [], []
    monkeypatch.setattr(app_module.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(app_module.os, "_exit", exits.append)

    app._recover_from_freeze(31.0)

    assert rescued == [10] and exits == [3] and launched
    note = json.loads((tmp_path / app_module.FREEZE_MARKER).read_text())
    assert note["rescued_dictation"] == 7 and note["meeting"] == 3

    # Next launch: one alert explaining what happened, then the note is gone.
    app._alerts = []
    app._announce_previous_freeze()
    assert len(app._alerts) == 1 and "Recent" in app._alerts[0][1] and "meeting" in app._alerts[0][1]
    assert not (tmp_path / app_module.FREEZE_MARKER).exists()


def test_one_broken_refresh_section_doesnt_stop_the_others(app_module):
    app = app_module.SpeechToTextApp.__new__(app_module.SpeechToTextApp)
    app._ticks, app._refresh_errors, calls = 0, set(), []

    def broken_permissions():
        raise RuntimeError("nope")

    def broken_meetings():
        raise RuntimeError("nope")

    app._check_permissions = broken_permissions
    app._refresh_meeting_ui = broken_meetings
    app._show_pending_alerts = lambda: calls.append("alerts")
    app._refresh_menu_items = lambda: calls.append("menu")
    app._refresh_status = lambda: calls.append("status")
    app._refresh(None)
    app._refresh(None)
    assert calls == ["alerts", "menu", "status"] * 2
    assert len(app._refresh_errors) == 2  # logged once each, not every tick
