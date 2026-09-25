"""The Notetaker around the core: disk writer, controller lifecycle, window API and its security."""

import json
import time
import urllib.error
import urllib.request

import numpy as np
import pytest

from speech_to_text.backend import AppBackend
from speech_to_text.history import HistoryStore
from speech_to_text.meeting_audio import PcmWriter
from speech_to_text.meeting_controller import MeetingController
from speech_to_text.meetings import MeetingStore
from speech_to_text.webui import WebUI

RATE = 16_000


def test_pcm_writer_keeps_the_track_in_step_with_the_clock(tmp_path):
    started = time.monotonic() - 2.0  # pretend the meeting started 2 s ago and no audio came yet
    writer = PcmWriter(tmp_path / "t.pcm", started)
    writer.write(np.full(RATE // 10, 0.5, dtype=np.float32))
    writer.close(total_seconds=3.0)
    audio = np.fromfile(tmp_path / "t.pcm", dtype="<i2")
    assert len(audio) == 3 * RATE  # silence filled in before, and padded to the full duration
    assert abs(np.argmax(audio > 0) / RATE - 1.9) < 0.05  # the real audio lands ~2 s in


class FakeRecorder:
    def __init__(self, system_audio=True):
        self.system_audio, self.folder, self.started = system_audio, None, 0.0

    @property
    def elapsed(self):
        return 42.0

    def start(self, folder):
        folder.mkdir(parents=True)
        (np.concatenate([np.zeros(RATE), np.sin(np.arange(2 * RATE) / 5) * 0.2]) * 32767).astype("<i2").tofile(
            folder / "me.pcm"
        )
        self.folder = folder
        return self.system_audio

    def stop(self):
        return 3.0


class FakeTranscriber:
    def detect_language(self, audio):
        return "en"

    def transcribe_segments(self, audio, language, prompt=None):
        return [(0.0, 1.0, "Let's ship it on Friday.")]


def wait_for(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def controller(tmp_path):
    store = MeetingStore(tmp_path / "history.db")
    ready = []
    return MeetingController(store, FakeTranscriber(), None, FakeRecorder(), on_notes_ready=ready.append), ready


def test_controller_records_then_transcribes_in_the_background(controller):
    meetings, ready = controller
    started = meetings.start()
    assert meetings.recording and meetings.start() == {"meeting_id": started["meeting_id"]}  # idempotent
    folder = meetings.recorder.folder
    stopped = meetings.stop()
    assert stopped["meeting_id"] == started["meeting_id"] and not meetings.recording

    assert wait_for(lambda: ready == [started["meeting_id"]])
    meeting = meetings.store.get(started["meeting_id"])
    assert meeting.status == "done" and meeting.duration_seconds == 3.0
    assert [s.text for s in meeting.segments] == ["Let's ship it on Friday."]
    assert not folder.exists()


def test_controller_resumes_meetings_interrupted_by_a_quit(tmp_path):
    store = MeetingStore(tmp_path / "history.db")
    recorder = FakeRecorder()
    folder = tmp_path / "meeting-audio" / "old"
    recorder.start(folder)
    interrupted = store.start(folder, system_audio=True)  # the app quit mid-meeting
    lost = store.start(tmp_path / "gone", system_audio=True)
    ready = []
    meetings = MeetingController(store, FakeTranscriber(), None, recorder, on_notes_ready=ready.append)
    meetings.resume_unfinished()
    assert wait_for(lambda: ready == [interrupted])
    assert store.get(interrupted).status == "done"
    assert store.get(lost).status == "failed"


def test_controller_waits_for_the_model(tmp_path):
    store = MeetingStore(tmp_path / "history.db")
    model = {"ready": False}
    ready = []
    meetings = MeetingController(store, FakeTranscriber(), None, FakeRecorder(),
                                 is_model_ready=lambda: model["ready"], on_notes_ready=ready.append)
    meeting_id = meetings.start()["meeting_id"]
    meetings.stop()
    assert wait_for(lambda: meetings.progress and "Waiting" in meetings.progress)
    assert ready == [] and meetings.busy
    model["ready"] = True
    assert wait_for(lambda: ready == [meeting_id])


def test_cannot_delete_the_meeting_being_recorded(controller):
    meetings, _ready = controller
    meeting_id = meetings.start()["meeting_id"]
    with pytest.raises(ValueError):
        meetings.delete(meeting_id)
    meetings.stop()


@pytest.fixture
def server(controller, tmp_path):
    meetings, ready = controller
    history = HistoryStore(tmp_path / "history.db")
    history.add(raw_text="bom dia", final_text="Bom dia!", language="pt", app_name="Slack")
    copied = []
    ui = WebUI(AppBackend(meetings, history, copied.append, lambda: True))
    url = ui.start()
    base = url.split("/#")[0]
    yield base, ui.token, meetings, ready, copied
    ui.stop()


def call(base, path, token=None, body=None):
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"X-Token": token} if token else {},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_window_api_requires_the_token(server):
    base, token, *_ = server
    with urllib.request.urlopen(base + "/", timeout=5) as response:  # the page itself carries no data
        assert b"Notetaker" in response.read()
    for bad in (None, "wrong"):
        with pytest.raises(urllib.error.HTTPError) as error:
            call(base, "/api/meetings", bad)
        assert error.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as error:
        call(base, "/api/meetings/start", "wrong", body={})
    assert error.value.code == 403


def test_window_api_end_to_end(server):
    base, token, meetings, ready, copied = server
    assert call(base, "/api/state", token)["meeting_recording"] is False

    meeting_id = call(base, "/api/meetings/start", token, body={})["meeting_id"]
    assert call(base, "/api/state", token)["meeting_recording"] is True
    call(base, "/api/meetings/stop", token, body={})
    assert wait_for(lambda: ready == [meeting_id])

    listed = call(base, "/api/meetings", token)
    assert [m["id"] for m in listed] == [meeting_id] and "segments" not in listed[0]
    detail = call(base, f"/api/meetings/{meeting_id}", token)
    assert detail["segments"][0]["text"] == "Let's ship it on Friday."
    assert detail["speaker_labels"] == {"me": "You", "others": "Others"}

    call(base, f"/api/meetings/{meeting_id}/rename", token, body={"title": "Release sync"})
    assert call(base, f"/api/meetings/{meeting_id}", token)["title"] == "Release sync"

    assert call(base, "/api/dictations?q=bom", token)[0]["text"] == "Bom dia!"
    assert call(base, "/api/dictations?q=zzz", token) == []

    call(base, "/api/copy", token, body={"text": "hello"})
    assert copied == ["hello"]

    call(base, f"/api/meetings/{meeting_id}/delete", token, body={})
    with pytest.raises(urllib.error.HTTPError) as error:
        call(base, f"/api/meetings/{meeting_id}", token)
    assert error.value.code == 404
