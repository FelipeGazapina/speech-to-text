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

from .cleanup import LLMCleaner, apply_replacements, basic_cleanup
from .config import CONFIG_PATH, Config
from .history import HistoryStore
from .hotkey import HotkeyListener
from .learning import Profile, build_profile, correction_pairs
from .paster import copy_text, frontmost_app_name, paste_text
from .prompts import whisper_prompt
from .recorder import Recorder, is_silent
from .transcriber import SAMPLE_RATE, Transcriber

log = logging.getLogger(__name__)

ICON_IDLE, ICON_RECORDING, ICON_WORKING, ICON_LOADING, ICON_ERROR = "🎙", "🔴", "💭", "⏳", "⚠️"
RECENT_COUNT = 10


def play_sound(name: str) -> None:
    subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])


@dataclass
class Job:
    audio: np.ndarray
    app_name: str | None


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
        self.profile = Profile()
        self._profile_stale = True
        self._menu_stale = True
        self.jobs: queue.Queue[Job] = queue.Queue()
        self.model_ready = False
        self.error: str | None = None
        self.last: LastDictation | None = None
        self.recording_started = 0.0
        self._pending_alert: str | None = None

        self.status_item = rumps.MenuItem("Starting…")
        self.cleanup_item = rumps.MenuItem("Smart cleanup (Ollama)", callback=self.toggle_cleanup)
        self.cleanup_item.state = int(config.cleanup.enabled)
        self.recent_menu = rumps.MenuItem("Recent (click to copy)")
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
            try:
                self._process(job)
                self.error = None
            except Exception as exc:
                log.exception("Dictation failed")
                self.error = f"Dictation failed: {exc}"
                if self.config.sounds:
                    play_sound("Basso")
            finally:
                self.jobs.task_done()

    def _process(self, job: Job) -> None:
        if is_silent(job.audio):
            log.info("Recording was empty or silent; nothing to paste")
            return
        profile = self._current_profile()
        vocabulary = [*self.config.cleanup.vocabulary, *profile.vocabulary]
        # Your explicit replacements win over learned ones.
        replacements = {**profile.replacements, **self.config.replacements}

        started = time.monotonic()
        language = self.transcriber.detect_language(job.audio)
        prompt = whisper_prompt(language, vocabulary, profile.recent_text.get(language or "", ""))
        raw = self.transcriber.transcribe(job.audio, language, prompt)
        transcribe_seconds = time.monotonic() - started
        log.info("Whisper [%s]: %r", language, raw)

        text = basic_cleanup(raw, replacements, language)
        if not text:
            return

        cleaned, cleanup_seconds = None, None
        if self.cleanup_item.state:
            started = time.monotonic()
            cleaned = self.cleaner.clean(
                text,
                language=language,
                vocabulary=profile.vocabulary,
                corrections=profile.corrections,
                examples=profile.examples,
            )
            cleanup_seconds = time.monotonic() - started
        final = apply_replacements(cleaned, replacements) if cleaned else text
        log.info("Pasting: %r", final)

        # Save before pasting, so the text survives even if the paste (or the app) fails.
        history_id = self._save(
            raw_text=raw,
            final_text=final,
            language=language,
            app_name=job.app_name,
            audio_seconds=len(job.audio) / SAMPLE_RATE,
            transcribe_seconds=transcribe_seconds,
            cleanup_seconds=cleanup_seconds,
            cleanup_used=cleaned is not None,
        )
        self.last = LastDictation(final, history_id)

        pasted = False
        try:
            pasted = paste_text(final + (" " if self.config.append_space and not final.endswith("\n") else ""))
        finally:
            if self.history and history_id is not None:
                try:
                    self.history.mark_pasted(history_id, pasted)
                except Exception:
                    log.exception("Could not record paste status")
            self._menu_stale = True
        if not pasted:
            self.error = "Couldn't paste (grant Accessibility). Text is on your clipboard."

    def _save(self, **fields) -> int | None:
        if not self.history:
            return None
        try:
            history_id = self.history.add(**fields)
        except Exception:
            log.exception("Could not save to history")
            return None
        self._profile_stale = True
        return history_id

    def _current_profile(self) -> Profile:
        if self._profile_stale and self.history and self.config.history.learn:
            try:
                self.profile = build_profile(self.history)
                log.info(
                    "Profile: %d learned terms, %d corrections, %d examples",
                    len(self.profile.vocabulary), len(self.profile.corrections), len(self.profile.examples),
                )
            except Exception:
                log.exception("Could not build the learning profile")
            self._profile_stale = False
        return self.profile

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
            text = item.best_text
            snippet = " ".join(text.split())
            snippet = snippet if len(snippet) <= 60 else snippet[:59] + "…"
            flag = "⚠️ " if item.pasted == 0 else ""
            title = f"{item.created_at[11:16]}  {flag}{snippet}"
            if title not in self.recent_menu:  # rumps keys items by title
                self.recent_menu.add(rumps.MenuItem(title, callback=lambda _, t=text: copy_text(t)))

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
            self._profile_stale = True
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
