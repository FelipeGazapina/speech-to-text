"""What the app window (webui.py) can ask for: meetings, dictations, the recording state."""

from __future__ import annotations

from typing import Callable

from .history import HistoryStore
from .meeting_controller import MeetingController


class AppBackend:
    def __init__(
        self,
        meetings: MeetingController,
        history: HistoryStore | None,
        copy_text: Callable[[str], None],
        is_model_ready: Callable[[], bool],
        start_meeting: Callable[[], dict] | None = None,
        stop_meeting: Callable[[], dict] | None = None,
    ):
        self.meetings = meetings
        self.history = history
        self._copy = copy_text
        self._model_ready = is_model_ready
        # The app wraps start/stop to add its sounds and permission hints.
        self._start = start_meeting or meetings.start
        self._stop = stop_meeting or meetings.stop

    def state(self) -> dict:
        recorder = self.meetings.recorder
        return {
            "meeting_recording": self.meetings.recording,
            "meeting_elapsed": recorder.elapsed if self.meetings.recording else 0,
            "meeting_progress": self.meetings.progress,
            "meetings_version": self.meetings.version,
            "model_ready": self._model_ready(),
        }

    def list_meetings(self) -> list[dict]:
        return [m.to_dict(with_segments=False) for m in self.meetings.store.list()]

    def get_meeting(self, meeting_id: int) -> dict | None:
        meeting = self.meetings.store.get(meeting_id)
        return meeting.to_dict() if meeting else None

    def start_meeting(self) -> dict:
        return self._start()

    def stop_meeting(self) -> dict:
        return self._stop()

    def summarize_meeting(self, meeting_id: int) -> dict:
        meeting = self.meetings.store.get(meeting_id)
        if meeting and meeting.status == "failed" and meeting.audio_dir:
            self.meetings.retry(meeting_id)  # transcription failed earlier: try again from the audio
        else:
            self.meetings.summarize(meeting_id)
        return {"ok": True}

    def rename_meeting(self, meeting_id: int, title: str) -> dict:
        if not title.strip():
            raise ValueError("The title can't be empty")
        self.meetings.rename(meeting_id, title)
        return {"ok": True}

    def delete_meeting(self, meeting_id: int) -> dict:
        self.meetings.delete(meeting_id)
        return {"ok": True}

    def list_dictations(self, search: str | None, limit: int) -> list[dict]:
        if not self.history:
            return []
        return [
            {
                "id": item.id,
                "created_at": item.created_at,
                "language": item.language,
                "app_name": item.app_name,
                "status": item.status,
                "text": item.best_text or "(transcription failed: retry it from the menu bar, under Recent)",
                "raw_text": item.raw_text,
            }
            for item in self.history.recent(max(1, min(limit, 2000)), search=search)
        ]

    def copy(self, text: str) -> dict:
        self._copy(text)
        return {"ok": True}
