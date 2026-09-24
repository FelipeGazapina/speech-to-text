"""Menu bar app: tap the hotkey to record, tap again to transcribe, clean up and paste."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np
import rumps

from .cleanup import LLMCleaner
from .config import CONFIG_PATH, Config
from .history import HistoryStore
from .hotkey import HotkeyListener
from .learning import correction_pairs
from .paster import copy_text, frontmost_app_name, paste_text
from .pipeline import DictationFailed, Pipeline
from .recorder import Recorder
from .transcriber import Transcriber

log = logging.getLogger(__name__)

ICON_IDLE, ICON_RECORDING, ICON_WORKING, ICON_LOADING, ICON_ERROR = "🎙", "🔴", "💭", "⏳", "⚠️"
RECENT_COUNT = 10
STATUS_ICONS = {"paste_failed": "⚠️ ", "filtered": "🔇 ", "failed": "❌ ", "recovered": "♻️ "}


def play_sound(name: str) -> None:
    subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])


@dataclass
class Job:
    audio: np.ndarray | None
    app_name: str | None = None
    retry_id: int | None = None  # re-transcribe a failed dictation from its saved audio


@dataclass
class LastDictation:
    text: str
    history_id: int | None


class SpeechToTextApp(rumps.App):
    def __init__(self, config: Config, log_path: str):
        super().__init__("Speech to Text", title=ICON_LOADING)
        self.config = config
        self.log_path = log_path
        self.recorder = Recorder()
        self.transcriber = Transcriber(config.transcription)
        self.cleaner = LLMCleaner(config.cleanup)
        self.history = self._open_history()
        self._menu_stale = True
        self.cleanup_item = rumps.MenuItem("Smart cleanup (Ollama)", callback=self.toggle_cleanup)
        self.cleanup_item.state = int(config.cleanup.enabled)
        self.pipeline = Pipeline(
            config, self.transcriber, self.cleaner, self.history, cleanup_enabled=lambda: bool(self.cleanup_item.state)
        )
        self.jobs: queue.Queue[Job] = queue.Queue()
        self.model_ready = False
        self.error: str | None = None
        self.last: LastDictation | None = None
        self.recording_started = 0.0
        self._pending_alert: str | None = None

        self.status_item = rumps.MenuItem("Starting…")
        self.recent_menu = rumps.MenuItem("Recent (click to copy, ❌ to retry)")
        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Start / stop recording", callback=lambda _: self.toggle_recording()),
            rumps.MenuItem("Copy last transcription", callback=self.copy_last),
            rumps.MenuItem("Fix last transcription…", callback=self.fix_last),
            self.recent_menu,
            None,
            self.cleanup_item,
            rumps.MenuItem("Open config", callback=lambda _: subprocess.Popen(["open", "-t", str(CONFIG_PATH)])),
            rumps.MenuItem("Reload config", callback=self.reload),
            rumps.MenuItem("Open log", callback=lambda _: subprocess.Popen(["open", self.log_path])),
            None,
        ]

        self._request_permissions()
        self.hotkey = HotkeyListener(config.hotkey, self.toggle_recording, self.cancel_recording)
        if not self.hotkey.start():
            self._pending_alert = (
                "The hotkey can't be captured yet. Give the app that launched this (e.g. Terminal) "
                "Input Monitoring AND Accessibility access in System Settings → Privacy & Security, "
                "then choose Reload config.\n\nYou can still use Start / stop recording from this menu."
            )

        threading.Thread(target=self._worker, daemon=True).start()
        self._timer = rumps.Timer(self._refresh, 0.25)
        self._timer.start()

    def _open_history(self) -> HistoryStore | None:
        if not self.config.history.enabled:
            return None
        try:
            return HistoryStore()
        except Exception:
            log.exception("Could not open the history database; continuing without history")
            return None

    # --- recording (called on the main thread, from the hotkey or the menu) ---

    def toggle_recording(self) -> None:
        if self.recorder.is_recording:
            audio = self.recorder.stop()
            if self.config.sounds:
                play_sound("Pop")
            self.jobs.put(Job(audio, frontmost_app_name()))
            return
        try:
            self.recorder.start()
        except Exception as exc:
            log.exception("Could not start recording")
            self.error = f"Microphone error: {exc}"
            if self.config.sounds:
                play_sound("Basso")
            return
        self.recording_started = time.monotonic()
        if self.config.sounds:
            play_sound("Tink")

    def cancel_recording(self) -> None:
        if self.config.esc_cancels and self.recorder.is_recording:
            self.recorder.stop()
            log.info("Recording cancelled")
            if self.config.sounds:
                play_sound("Funk")

    # --- transcription pipeline (background thread, one job at a time so pastes stay in order) ---

    def _worker(self) -> None:
        try:
            self.transcriber.load()
        except Exception as exc:
            log.exception("Failed to load the Whisper model")
            self.error = f"Model failed to load: {exc}"
        self.model_ready = True
        try:
            self.cleaner.warm_up()
        except Exception:
            log.exception("Cleanup model warm-up failed")

        while True:
            job = self.jobs.get()
            self.error = None
            try:
                self._process(job)
            except Exception as exc:
                log.exception("Dictation failed")
                self.error = str(exc) if isinstance(exc.__cause__, DictationFailed) else f"Dictation failed: {exc}"
                if self.config.sounds:
                    play_sound("Basso")
            finally:
                self.jobs.task_done()

    def _process(self, job: Job) -> None:
        if job.retry_id is not None:
            text = self.pipeline.retry(job.retry_id)
            copy_text(text)
            self.last = LastDictation(text, job.retry_id)
            self._menu_stale = True
            log.info("Recovered #%d: %r", job.retry_id, text)
            if self.config.sounds:
                play_sound("Glass")
            return

        try:
            outcome = self.pipeline.process(job.audio, job.app_name)
        except DictationFailed as failure:
            self._menu_stale = True
            where = "Audio saved: click it under Recent to retry." if failure.history_id else ""
            raise RuntimeError(f"Transcription failed. {where}") from failure
        if outcome.history_id is not None:
            self._menu_stale = True
        if not outcome.text:
            return

        final = outcome.text
        log.info("Pasting: %r", final)
        self.last = LastDictation(final, outcome.history_id)
        pasted = False
        try:
            pasted = paste_text(final + (" " if self.config.append_space and not final.endswith("\n") else ""))
        finally:
            if self.history and outcome.history_id is not None:
                try:
                    self.history.set_status(outcome.history_id, "pasted" if pasted else "paste_failed")
                except Exception:
                    log.exception("Could not record paste status")
        if not pasted:
            self.error = "Couldn't paste (grant Accessibility). Text is on your clipboard."

    # --- menu bar ---

    def _refresh(self, _timer) -> None:
        if self._pending_alert:
            message, self._pending_alert = self._pending_alert, None
            rumps.alert("Speech to Text needs permissions", message)
        if self._menu_stale:
            self._menu_stale = False
            self._rebuild_recent_menu()

        hotkey = self.config.hotkey.replace("_", " ")
        if self.recorder.is_recording:
            elapsed = int(time.monotonic() - self.recording_started)
            title, status = f"{ICON_RECORDING} {elapsed // 60}:{elapsed % 60:02d}", f"Recording… tap {hotkey} to stop"
        elif self.jobs.unfinished_tasks and self.model_ready:
            title, status = ICON_WORKING, "Transcribing…"
        elif not self.model_ready:
            title, status = ICON_LOADING, "Loading Whisper model (first run downloads it)…"
        elif self.error:
            title, status = ICON_ERROR, self.error[:120]
        else:
            title, status = ICON_IDLE, f"Ready — tap {hotkey} to dictate"
        if self.title != title:
            self.title = title
        if self.status_item.title != status:
            self.status_item.title = status

    def _rebuild_recent_menu(self) -> None:
        if self.recent_menu._menu is not None:
            self.recent_menu.clear()
        items = []
        if self.history:
            try:
                items = self.history.recent(RECENT_COUNT)
            except Exception:
                log.exception("Could not read history")
        if not items:
            self.recent_menu.add(rumps.MenuItem("Nothing yet" if self.history else "History is off"))
            return
        for item in items:
            if item.status == "failed":
                snippet, callback = "transcription failed — click to retry", self._retry_callback(item.id)
            else:
                snippet = " ".join(item.best_text.split())
                snippet = snippet if len(snippet) <= 60 else snippet[:59] + "…"
                callback = lambda _, t=item.best_text: copy_text(t)
            title = f"{item.created_at[11:16]}  {STATUS_ICONS.get(item.status, '')}{snippet}"
            if title not in self.recent_menu:  # rumps keys items by title
                self.recent_menu.add(rumps.MenuItem(title, callback=callback))

    def _retry_callback(self, history_id: int):
        def retry(_item) -> None:
            self.jobs.put(Job(None, retry_id=history_id))

        return retry

    def toggle_cleanup(self, item) -> None:
        item.state = not item.state

    def copy_last(self, _item) -> None:
        if self.last:
            copy_text(self.last.text)

    def fix_last(self, _item) -> None:
        """Edit the last dictation. The fix is copied, saved, and learned from."""
        if not self.last:
            rumps.alert("Nothing to fix yet", "Dictate something first.")
            return
        from AppKit import NSApp

        NSApp.activateIgnoringOtherApps_(True)
        response = rumps.Window(
            title="Fix last transcription",
            message="Correct the text. It's copied to your clipboard, and the app learns from your fix.",
            default_text=self.last.text,
            ok="Save & copy",
            cancel="Cancel",
            dimensions=(480, 140),
        ).run()
        corrected = response.text.strip()
        if not response.clicked or not corrected or corrected == self.last.text:
            return
        copy_text(corrected)
        if self.history and self.last.history_id is not None:
            pairs = correction_pairs(self.last.text, corrected)
            self.history.set_correction(self.last.history_id, corrected, pairs)
            log.info("Learned corrections: %s", pairs)
            self.pipeline.mark_profile_stale()
            self._menu_stale = True
        self.last = LastDictation(corrected, self.last.history_id)

    def reload(self, _item) -> None:
        os.execv(sys.executable, [sys.executable, "-m", "speech_to_text"])

    def _request_permissions(self) -> None:
        """Trigger the macOS permission prompts up front instead of failing silently later."""
        try:
            import Quartz

            if hasattr(Quartz, "CGRequestListenEventAccess") and not Quartz.CGPreflightListenEventAccess():
                Quartz.CGRequestListenEventAccess()  # Input Monitoring: needed to see the hotkey
        except Exception:
            log.exception("Input Monitoring check failed")
        try:
            from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

            if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
                log.warning("Accessibility permission missing: pasting (Cmd+V) won't work until granted")
        except Exception:
            log.exception("Accessibility check failed")
