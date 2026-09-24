"""Menu bar app: tap the hotkey to record, tap again to transcribe, clean up and paste."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time

import numpy as np
import rumps

from .cleanup import LLMCleaner, basic_cleanup
from .config import CONFIG_PATH, Config
from .hotkey import HotkeyListener
from .paster import copy_text, paste_text
from .prompts import whisper_prompt
from .recorder import Recorder, is_silent
from .transcriber import Transcriber

log = logging.getLogger(__name__)

ICON_IDLE, ICON_RECORDING, ICON_WORKING, ICON_LOADING, ICON_ERROR = "🎙", "🔴", "💭", "⏳", "⚠️"


def play_sound(name: str) -> None:
    subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])


class SpeechToTextApp(rumps.App):
    def __init__(self, config: Config, log_path: str):
        super().__init__("Speech to Text", title=ICON_LOADING)
        self.config = config
        self.log_path = log_path
        self.recorder = Recorder()
        self.transcriber = Transcriber(config.transcription, whisper_prompt(config.cleanup.vocabulary))
        self.cleaner = LLMCleaner(config.cleanup)
        self.jobs: queue.Queue[np.ndarray] = queue.Queue()
        self.model_ready = False
        self.error: str | None = None
        self.last_text = ""
        self.recording_started = 0.0
        self._pending_alert: str | None = None

        self.status_item = rumps.MenuItem("Starting…")
        self.cleanup_item = rumps.MenuItem("Smart cleanup (Ollama)", callback=self.toggle_cleanup)
        self.cleanup_item.state = int(config.cleanup.enabled)
        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Start / stop recording", callback=lambda _: self.toggle_recording()),
            rumps.MenuItem("Copy last transcription", callback=self.copy_last),
            self.cleanup_item,
            None,
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

    # --- recording (called on the main thread, from the hotkey or the menu) ---

    def toggle_recording(self) -> None:
        if self.recorder.is_recording:
            audio = self.recorder.stop()
            if self.config.sounds:
                play_sound("Pop")
            self.jobs.put(audio)
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
            audio = self.jobs.get()
            try:
                self._process(audio)
                self.error = None
            except Exception as exc:
                log.exception("Dictation failed")
                self.error = f"Dictation failed: {exc}"
                if self.config.sounds:
                    play_sound("Basso")
            finally:
                self.jobs.task_done()

    def _process(self, audio: np.ndarray) -> None:
        if is_silent(audio):
            log.info("Recording was empty or silent; nothing to paste")
            return
        raw = self.transcriber.transcribe(audio)
        log.info("Whisper: %r", raw)
        text = basic_cleanup(raw, self.config.replacements)
        if not text:
            return
        if self.cleanup_item.state:
            text = self.cleaner.clean(text) or text
        log.info("Pasting: %r", text)
        self.last_text = text
        if self.config.append_space and not text.endswith("\n"):
            text += " "
        paste_text(text)

    # --- menu bar ---

    def _refresh(self, _timer) -> None:
        if self._pending_alert:
            message, self._pending_alert = self._pending_alert, None
            rumps.alert("Speech to Text needs permissions", message)

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

    def toggle_cleanup(self, item) -> None:
        item.state = not item.state

    def copy_last(self, _item) -> None:
        if self.last_text:
            copy_text(self.last_text)

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
