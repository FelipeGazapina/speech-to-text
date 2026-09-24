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
from .config import CONFIG_PATH, Config, set_top_level_value
from .history import HistoryStore
from .history_page import write_history_page
from .hotkey import HotkeyListener
from .learning import correction_pairs
from .paster import copy_text, frontmost_app_name, paste_text
from . import permissions
from .pipeline import DictationFailed, Pipeline
from .recorder import Recorder
from .transcriber import Transcriber

log = logging.getLogger(__name__)

ICON_IDLE, ICON_RECORDING, ICON_WORKING, ICON_LOADING, ICON_ERROR = "🎙", "🔴", "💭", "⏳", "⚠️"
RECENT_COUNT = 10
STATUS_ICONS = {"paste_failed": "⚠️ ", "filtered": "🔇 ", "failed": "❌ ", "recovered": "♻️ "}
FROZEN = getattr(sys, "frozen", False)  # running as the packaged Speech to Text.app
HOTKEY_CHOICES = {
    "Right Option (⌥)": "right_option",
    "Left Option (⌥)": "left_option",
    "Right Command (⌘)": "right_command",
    "Right Control (⌃)": "right_control",
    "Fn / Globe (🌐)": "fn",
    "F18 (for remapped keys)": "f18",
}
CLEANUP_SETUP_TITLES = {
    "checking": "Smart cleanup: checking…",
    "disabled": "Smart cleanup: turned off in config",
    "no_ollama": "Smart cleanup: install Ollama (free)…",
    "missing_model": "Smart cleanup: model not downloaded yet",
    "downloading": "Smart cleanup: downloading model (one time, ~2 GB)…",
    "ready": "Smart cleanup: ready ✓",
    "error": "Smart cleanup: setup failed (see log)",
}
WELCOME = (
    "Speech to Text lives in your menu bar (🎙).\n\n"
    "• Tap {hotkey} once to start talking, tap it again to stop. The text is pasted where your cursor is.\n"
    "• macOS will ask for Microphone, Accessibility and Input Monitoring access: allow all three. "
    "If one is missing, the 🎙 menu shows ⚠️ with a button that opens the right settings page.\n"
    "• The first start downloads the speech model (~1.6 GB), which takes a few minutes. The icon shows ⏳ meanwhile.\n"
    "• For smarter text (removing \"um\", applying \"no wait…\" corrections), install the free Ollama app; "
    "the 🎙 menu has a link."
)


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
    def __init__(self, config: Config, log_path: str, first_run: bool = False):
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
        self.cleanup_state = "checking"
        self._alerts: list[tuple[str, str]] = []

        self.status_item = rumps.MenuItem("Starting…")
        self.permission_item = rumps.MenuItem("Permissions: checking…")
        self.recent_menu = rumps.MenuItem("Recent (click to copy, ❌ to retry)")
        self.setup_item = rumps.MenuItem(CLEANUP_SETUP_TITLES["checking"])
        self.hotkey_menu = rumps.MenuItem("Hotkey")
        for label, key in HOTKEY_CHOICES.items():
            choice = rumps.MenuItem(label, callback=lambda _, k=key: self.change_hotkey(k))
            choice.state = int(key == config.hotkey)
            self.hotkey_menu.add(choice)
        advanced = rumps.MenuItem("Advanced")
        for item in (
            rumps.MenuItem("Open config file", callback=lambda _: subprocess.Popen(["open", "-t", str(CONFIG_PATH)])),
            rumps.MenuItem("Open log", callback=lambda _: subprocess.Popen(["open", self.log_path])),
            rumps.MenuItem("Show log files in Finder", callback=lambda _: subprocess.Popen(["open", "-R", self.log_path])),
        ):
            advanced.add(item)
        self.login_item = rumps.MenuItem("Open at login", callback=self.toggle_login_item)
        self.login_item.state = int(_login_item_enabled())

        self.menu = [
            self.status_item,
            self.permission_item,
            None,
            rumps.MenuItem("Start / stop recording", callback=lambda _: self.toggle_recording()),
            rumps.MenuItem("Copy last transcription", callback=self.copy_last),
            rumps.MenuItem("Fix last transcription…", callback=self.fix_last),
            self.recent_menu,
            rumps.MenuItem("Show all history…", callback=self.show_history),
            None,
            self.cleanup_item,
            self.setup_item,
            self.hotkey_menu,
            *([self.login_item] if FROZEN else []),
            advanced,
            None,
            rumps.MenuItem("Restart", callback=self.reload),
        ]

        hotkey_label = next((label for label, key in HOTKEY_CHOICES.items() if key == config.hotkey), config.hotkey)
        if first_run:
            self._alerts.append(("Welcome to Speech to Text", WELCOME.format(hotkey=hotkey_label)))
        permissions.request_all()
        self.missing_permissions = permissions.missing_permissions()
        self.hotkey = HotkeyListener(config.hotkey, self.toggle_recording, self.cancel_recording)
        # macOS only delivers keys to a listener created after Input Monitoring was granted, so once
        # it's granted while we're running, we restart by ourselves.
        self._restart_when_listening_allowed = permissions.INPUT_MONITORING in self.missing_permissions
        if not self.hotkey.start() and not self._restart_when_listening_allowed:
            self._alerts.append((
                "The hotkey isn't working",
                "macOS refused to let Speech to Text watch the keyboard. Check Input Monitoring and Accessibility "
                "in System Settings → Privacy & Security, then choose Restart from the 🎙 menu.\n\n"
                "You can still use Start / stop recording from the menu meanwhile.",
            ))
        if self.missing_permissions:
            log.warning("Missing permissions: %s", ", ".join(self.missing_permissions))
        self._ticks = 0

        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._setup_cleanup, daemon=True).start()
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
            log.info("Recording stopped (%.1fs)", time.monotonic() - self.recording_started)
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
        log.info("Recording started")
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

    def _setup_cleanup(self) -> None:
        """Make the smart cleanup work without any Terminal commands: detect Ollama, download the model."""
        while True:
            try:
                state = self.cleaner.setup_state()
                if state == "missing_model":
                    self.cleanup_state = "downloading"
                    self.cleaner.download_model()
                    state = self.cleaner.setup_state()
                if state == "ready":
                    self.cleaner.warm_up()
                self.cleanup_state = state
            except Exception:
                log.exception("Smart cleanup setup failed")
                self.cleanup_state = "error"
            if self.cleanup_state in ("ready", "disabled"):
                return
            time.sleep(30)  # e.g. waiting for you to install or start Ollama

    def _check_permissions(self) -> None:
        self.missing_permissions = permissions.missing_permissions()
        if self.missing_permissions:
            first = self.missing_permissions[0]
            title = f"⚠️ Allow {first} ({permissions.WHY[first]})…"
            callback = lambda _, p=first: permissions.open_settings(p)  # noqa: E731
        else:
            title, callback = "Permissions: all set ✓", None
        if self.permission_item.title != title:
            self.permission_item.title = title
            self.permission_item.set_callback(callback)
        idle = not self.recorder.is_recording and not self.jobs.unfinished_tasks
        if self._restart_when_listening_allowed and permissions.INPUT_MONITORING not in self.missing_permissions and idle:
            log.info("Input Monitoring was just granted: restarting so the hotkey starts working")
            self.reload(None)

    def _refresh(self, _timer) -> None:
        self._ticks += 1
        if self._ticks % 8 == 1:  # every 2 seconds
            self._check_permissions()
        if self._alerts:
            title, message = self._alerts.pop(0)
            rumps.alert(title, message)
        setup_title = CLEANUP_SETUP_TITLES.get(self.cleanup_state, self.cleanup_state)
        if self.setup_item.title != setup_title:
            self.setup_item.title = setup_title
            self.setup_item.set_callback(self.open_ollama_download if self.cleanup_state == "no_ollama" else None)
        if self._menu_stale:
            self._menu_stale = False
            self._rebuild_recent_menu()

        hotkey = self.config.hotkey.replace("_", " ")
        if self.recorder.is_recording:
            elapsed = int(time.monotonic() - self.recording_started)
            title, status = f"{ICON_RECORDING} {elapsed // 60}:{elapsed % 60:02d}", f"Recording… tap {hotkey} to stop"
        elif self.jobs.unfinished_tasks and self.model_ready:
            title, status = ICON_WORKING, "Transcribing…"
        elif self.missing_permissions:
            title, status = ICON_ERROR, f"Needs permission: {', '.join(self.missing_permissions)} (see below)"
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

    def open_ollama_download(self, _item) -> None:
        subprocess.Popen(["open", "https://ollama.com/download"])

    def show_history(self, _item) -> None:
        if not self.history:
            rumps.alert("History is off", "Turn it on with [history] enabled = true in the config file.")
            return
        subprocess.Popen(["open", str(write_history_page(self.history))])

    def change_hotkey(self, key: str) -> None:
        if key == self.config.hotkey:
            return
        set_top_level_value("hotkey", key)
        self.reload(None)

    def toggle_login_item(self, item) -> None:
        try:
            from ServiceManagement import SMAppService

            service = SMAppService.mainAppService()
            if item.state:
                ok, error = service.unregisterAndReturnError_(None)
            else:
                ok, error = service.registerAndReturnError_(None)
            if not ok:
                raise RuntimeError(error)
        except Exception as exc:
            log.exception("Could not change the login item")
            rumps.alert(
                "Couldn't change Open at login",
                f"{exc}\n\nYou can add it yourself: System Settings → General → Login Items → +.",
            )
        item.state = int(_login_item_enabled())

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
        """Restart the app (picks up config changes and newly granted permissions)."""
        if FROZEN:
            os.execv(sys.executable, [sys.executable])
        os.execv(sys.executable, [sys.executable, "-m", "speech_to_text"])


def _login_item_enabled() -> bool:
    if not FROZEN:
        return False
    try:
        from ServiceManagement import SMAppService

        return SMAppService.mainAppService().status() == 1  # SMAppServiceStatusEnabled
    except Exception:
        return False
