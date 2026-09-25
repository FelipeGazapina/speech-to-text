"""Meeting notes lifecycle, independent of any UI: start/stop recording, a background queue that
transcribes and summarizes finished meetings, and recovery of meetings interrupted by a quit."""

from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from .meeting_notes import Summarizer, process_meeting, summarize_meeting
from .meetings import MeetingStore

log = logging.getLogger(__name__)


class MeetingController:
    def __init__(
        self,
        store: MeetingStore,
        transcriber,
        summarizer: Summarizer | None,
        recorder,
        is_model_ready: Callable[[], bool] = lambda: True,
        on_notes_ready: Callable[[int], None] | None = None,
    ):
        self.store = store
        self.transcriber = transcriber
        self.summarizer = summarizer
        self.recorder = recorder
        self.is_model_ready = is_model_ready
        self.on_notes_ready = on_notes_ready
        self.current_id: int | None = None
        self.progress: str | None = None  # e.g. "Transcribing the meeting… 40%"
        self.version = 0  # bumped on every change, so the window knows when to refresh
        self.missing_system_audio = False  # the last meeting couldn't record the computer's audio
        self._lock = threading.Lock()
        self._queue: queue.Queue[int] = queue.Queue()
        threading.Thread(target=self._worker, name="meetings", daemon=True).start()

    @property
    def recording(self) -> bool:
        return self.current_id is not None

    @property
    def busy(self) -> bool:
        return self.recording or self.progress is not None or not self._queue.empty()

    def _changed(self) -> None:
        self.version += 1

    # --- recording ---

    def start(self) -> dict:
        with self._lock:
            if self.current_id is not None:
                return {"meeting_id": self.current_id}
            folder = self.store.temp_dir / time.strftime("%Y%m%d-%H%M%S")
            system_audio = self.recorder.start(folder)
            self.current_id = self.store.start(folder, system_audio)
            self.missing_system_audio = not system_audio
            log.info("Meeting #%d started (computer audio: %s)", self.current_id, system_audio)
            self._changed()
            return {"meeting_id": self.current_id, "system_audio": system_audio}

    def stop(self) -> dict:
        with self._lock:
            if self.current_id is None:
                return {}
            meeting_id, self.current_id = self.current_id, None
            duration = self.recorder.stop()
            self.store.update(meeting_id, duration_seconds=duration, status="processing")
            log.info("Meeting #%d stopped after %.0fs", meeting_id, duration)
            self._queue.put(meeting_id)
            self._changed()
            return {"meeting_id": meeting_id, "message": "Transcribing the meeting…"}

    def resume_unfinished(self) -> None:
        """Meetings cut short by a quit or crash: transcribe what was recorded."""
        for meeting in self.store.unfinished():
            if meeting.audio_dir and Path(meeting.audio_dir).exists():
                log.info("Resuming interrupted meeting #%d", meeting.id)
                self.store.update(meeting.id, status="processing")
                self._queue.put(meeting.id)
            else:
                self.store.update(meeting.id, status="failed", error="The recording was lost.")
        self._changed()

    def retry(self, meeting_id: int) -> None:
        self._queue.put(meeting_id)
        self._changed()

    # --- edits ---

    def summarize(self, meeting_id: int) -> None:
        if not self.summarizer:
            return

        def run() -> None:
            summarize_meeting(meeting_id, self.store, self.summarizer, self._set_progress)
            self.progress = None
            self._changed()

        threading.Thread(target=run, name="meeting-summary", daemon=True).start()

    def rename(self, meeting_id: int, title: str) -> None:
        self.store.update(meeting_id, title=title.strip()[:200])
        self._changed()

    def delete(self, meeting_id: int) -> None:
        if meeting_id == self.current_id:
            raise ValueError("Stop the recording before deleting this meeting")
        meeting = self.store.get(meeting_id)
        if meeting and meeting.audio_dir:
            shutil.rmtree(meeting.audio_dir, ignore_errors=True)
        self.store.delete(meeting_id)
        self._changed()

    # --- background processing ---

    def _set_progress(self, message: str) -> None:
        self.progress = message
        self._changed()

    def _worker(self) -> None:
        while True:
            meeting_id = self._queue.get()
            while not self.is_model_ready():
                self.progress = "Waiting for the speech model to load…"
                time.sleep(1)
            try:
                process_meeting(meeting_id, self.store, self.transcriber, self.summarizer, self._set_progress)
                if self.on_notes_ready:
                    self.on_notes_ready(meeting_id)
            except Exception:
                log.exception("Processing meeting #%d failed", meeting_id)
            finally:
                self.progress = None
                self._changed()
