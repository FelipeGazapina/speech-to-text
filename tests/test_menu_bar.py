"""The menu bar title tells you what's happening: a live loudness bar while recording, a spinner
and a percentage while transcribing, and an estimate of the time left."""

import threading
import time
import types

from test_resilience import app_module  # noqa: F401  (the fixture)

from speech_to_text.meeting_notes import Progress


def menu_app(app_module, **state):
    app = app_module.SpeechToTextApp.__new__(app_module.SpeechToTextApp)
    app.config = types.SimpleNamespace(hotkey="right_option")
    app.status_item = types.SimpleNamespace(title="")
    app.title = ""
    app._ticks = 0
    app.recorder = types.SimpleNamespace(is_recording=False, level=0.0)
    app.recording_started = 0.0
    app.jobs = types.SimpleNamespace(unfinished_tasks=0)
    app.model_ready = True
    app.download_progress = None
    app.missing_permissions = []
    app.error = None
    app.meetings = types.SimpleNamespace(
        progress=None, recording=False, levels={}, recorder=types.SimpleNamespace(elapsed=0.0)
    )
    for name, value in state.items():
        setattr(app, name, value)
    return app


def test_recording_shows_a_live_loudness_bar_and_the_time(app_module):
    app = menu_app(app_module, recorder=types.SimpleNamespace(is_recording=True, level=0.0003))
    app.recording_started = time.monotonic() - 12
    app._refresh_status()
    assert app.title == "🔴 ▁ 0:12"
    app.recorder.level = 0.1  # someone talking
    app._refresh_status()
    assert app.title.startswith("🔴 ▆")
    assert "tap right option to stop" in app.status_item.title


def test_meeting_transcription_shows_percent_spinner_and_time_left(app_module):
    app = menu_app(app_module)
    app.meetings.progress = Progress("Transcribing the meeting… 37%", 37, 700)
    app._refresh_status()
    assert app.title == "📝 ◐ 37%"
    assert app.status_item.title == "Transcribing the meeting… 37% · about 12 min left"
    app._ticks += 1
    app._refresh_status()
    assert app.title == "📝 ◓ 37%"  # it moves, so a stuck app is visibly stuck


def test_a_dictation_goes_first_while_the_meeting_is_transcribed(app_module):
    app = menu_app(app_module, jobs=types.SimpleNamespace(unfinished_tasks=1))
    app.meetings.progress = Progress("Transcribing the meeting… 37%", 37, 700)
    app._refresh_status()
    assert app.title.startswith("💭 ")
    assert app.status_item.title == "Transcribing your dictation… (the meeting notes wait their turn)"


def test_meeting_recording_shows_both_tracks_being_heard(app_module):
    app = menu_app(app_module)
    app.meetings.recording = True
    app.meetings.levels = {"me": 0.0002, "others": 0.05}
    app.meetings.recorder.elapsed = 3725
    app._refresh_status()
    assert app.title == "📝 ▅ 1:02:05"
    assert app.status_item.title == "Recording meeting notes · you ▁ · others ▅ (dictation still works)"


def test_a_second_click_while_the_meeting_is_stopping_is_ignored(app_module):
    app = app_module.SpeechToTextApp.__new__(app_module.SpeechToTextApp)
    app.meetings = types.SimpleNamespace(recording=True, phase="recording")
    app.meeting_item = types.SimpleNamespace(title="")
    app._notes_ready = []
    stopping = threading.Event()
    stopped = threading.Event()

    def stop_meeting():
        app.meetings.recording, app.meetings.phase = False, "stopping"  # the recorder is still closing
        stopping.wait()
        app.meetings.phase = "idle"
        stopped.set()

    app._stop_meeting = stop_meeting
    app._start_meeting = lambda: (_ for _ in ()).throw(AssertionError("a new meeting was started"))

    app.toggle_meeting()
    for _ in range(50):  # the stop runs on its own thread
        if app.meetings.phase == "stopping":
            break
        time.sleep(0.01)
    app.toggle_meeting()  # the impatient second click
    app._refresh_meeting_ui()
    assert app.meeting_item.title == "Stopping meeting notes…"
    stopping.set()
    assert stopped.wait(2)
    app._refresh_meeting_ui()
    assert app.meeting_item.title == "📝 Start meeting notes"
