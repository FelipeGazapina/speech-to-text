"""Menu bar app: tap the hotkey to record, tap again to transcribe, clean up and paste."""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rumps

from .cleanup import LLMCleaner
from .config import CONFIG_PATH, DATA_DIR, Config, set_top_level_value
from .backend import AppBackend
from .history import HistoryStore
from .hotkey import HotkeyListener
from .meeting_audio import MeetingRecorder
from .meeting_controller import MeetingController
from .meeting_notes import Summarizer
from .meetings import MeetingStore
from .learning import correction_pairs
from .paster import copy_text, frontmost_app_name, paste_text
from . import __version__, permissions, updater
from .pipeline import DictationFailed, Pipeline
from .recorder import Recorder
from .transcriber import Transcriber
from .watchdog import MainThreadWatchdog, Timeout
from .webui import WebUI
from .window import AppWindow

log = logging.getLogger(__name__)

ICON_IDLE, ICON_RECORDING, ICON_WORKING, ICON_LOADING, ICON_ERROR = "🎙", "🔴", "💭", "⏳", "⚠️"
ICON_MEETING = "📝"
RECENT_COUNT = 10
# A dictation that waited longer than this to be transcribed isn't pasted: the cursor has probably
# moved on, and text appearing out of nowhere is worse than finding it under Recent.
LATE_PASTE_SECONDS = 20
# If the main thread (menu bar, hotkey) doesn't respond for this long, the app saves any recording in
# progress and restarts itself.
FREEZE_LIMIT_SECONDS = 45
FREEZE_MARKER = "last-freeze.json"
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
    stopped_at: float = 0.0  # time.monotonic() when the recording ended


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
        self.download_progress: tuple[int, int] | None = None  # (bytes done, total) of the Whisper model
        self._paste_help_pending = False
        self._paste_help_shown = False
        self.error: str | None = None
        self.last: LastDictation | None = None
        self.recording_started = 0.0
        self.cleanup_state = "checking"
        self._alerts: list[tuple[str, str]] = []
        self._restart_suggestion: tuple[str, str] | None = None
        self._refresh_errors: set[str] = set()
        self._notes_ready: list[int] = []

        # Meeting notes (the Notetaker) and the app window that shows them.
        self.meetings = MeetingController(
            MeetingStore(),
            self.transcriber,
            Summarizer(config.cleanup),
            MeetingRecorder(),
            is_model_ready=lambda: self.model_ready,
            on_notes_ready=self._notes_ready.append,
        )
        self.meetings.resume_unfinished()
        self.webui = WebUI(AppBackend(
            self.meetings, self.history, copy_text, lambda: self.model_ready,
            start_meeting=self._start_meeting, stop_meeting=self._stop_meeting,
        ))
        self.window = AppWindow()
        self._meeting_hint_shown = False

        self.status_item = rumps.MenuItem("Starting…")
        self.meeting_item = rumps.MenuItem(f"{ICON_MEETING} Start meeting notes", callback=self.toggle_meeting)
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
        self.update_item = rumps.MenuItem("Check for updates", callback=self.check_for_updates)
        self.available_update: updater.Update | None = None
        self.update_status: str | None = None  # shown on the update menu item while downloading
        self._update_prompt: updater.Update | None = None
        self._update_ready_to_relaunch = False
        self.login_item.state = int(_login_item_enabled())

        self.menu = [
            self.status_item,
            self.permission_item,
            None,
            self.meeting_item,
            rumps.MenuItem("Open Speech to Text…", callback=lambda _: self.open_window()),
            None,
            rumps.MenuItem("Start / stop recording", callback=lambda _: self.toggle_recording()),
            rumps.MenuItem("Copy last transcription", callback=self.copy_last),
            rumps.MenuItem("Fix last transcription…", callback=self.fix_last),
            self.recent_menu,
            None,
            self.cleanup_item,
            self.setup_item,
            self.hotkey_menu,
            *([self.login_item, self.update_item] if FROZEN else []),
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
        if FROZEN:
            threading.Thread(target=self._update_loop, daemon=True).start()
        self._timer = rumps.Timer(self._refresh, 0.25)
        self._timer.start()
        # App Nap would slow a menu-bar-only app's timers (and its hotkey) while it sits in the background;
        # that would also look like a freeze to the watchdog.
        try:
            from Foundation import NSActivityUserInitiatedAllowingIdleSystemSleep, NSProcessInfo

            self._no_app_nap = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                NSActivityUserInitiatedAllowingIdleSystemSleep, "Listening for the dictation hotkey"
            )
        except Exception:
            log.exception("Couldn't opt out of App Nap")
        # The watchdog's heartbeat runs in every run loop mode, so an open menu or alert isn't a "freeze".
        self.watchdog = MainThreadWatchdog(self._recover_from_freeze, limit=FREEZE_LIMIT_SECONDS).start()
        self._heartbeat = rumps.Timer(self.watchdog.beat, 1.0)
        self._heartbeat.start()
        try:
            from Foundation import NSRunLoop, NSRunLoopCommonModes

            NSRunLoop.currentRunLoop().addTimer_forMode_(self._heartbeat._nstimer, NSRunLoopCommonModes)
        except Exception:
            log.exception("Couldn't schedule the watchdog heartbeat in all run loop modes")
        self._announce_previous_freeze()

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
            self.jobs.put(Job(audio, frontmost_app_name(), stopped_at=time.monotonic()))
            return
        if not self.model_ready:
            # Recording now would only queue text to paste minutes later, wherever the cursor is by then.
            log.info("Hotkey pressed while the speech model is still loading")
            if self.config.sounds:
                play_sound("Basso")
            return
        try:
            self.recorder.start()
        except Timeout:
            self.error = "The microphone didn't respond. Try again, or restart the app."
            self._restart_suggestion = (
                "The microphone isn't responding",
                "macOS's audio system didn't answer in time (this can happen when a Bluetooth headset "
                "connects or switches modes). Restarting Speech to Text usually fixes it.",
            )
            if self.config.sounds:
                play_sound("Basso")
            return
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
            self.transcriber.download(lambda done, total: setattr(self, "download_progress", (done, total)))
        except Exception:
            log.exception("Model download failed; trying to load it anyway")
        self.download_progress = None
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
                if self.transcriber.timeouts >= 2:
                    self._restart_suggestion = (
                        "Transcription keeps timing out",
                        "Your recordings are saved under Recent (❌) so nothing is lost. Restarting Speech to "
                        "Text usually clears this; after the restart, click them to transcribe them.",
                    )
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
        waited = time.monotonic() - job.stopped_at if job.stopped_at else 0.0
        if waited > LATE_PASTE_SECONDS:
            log.info("Not pasting: the dictation waited %.0fs; it's saved under Recent", waited)
            self.last = LastDictation(outcome.text, outcome.history_id)
            self.error = "A dictation finished late, so it wasn't pasted. It's under Recent."
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
            self.error = "Couldn't paste (allow Accessibility). The text is on your clipboard: press ⌘V."
            self._paste_help_pending = True

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
        idle = not self.recorder.is_recording and not self.jobs.unfinished_tasks and not self.meetings.busy
        if self._restart_when_listening_allowed and permissions.INPUT_MONITORING not in self.missing_permissions and idle:
            log.info("Input Monitoring was just granted: restarting so the hotkey starts working")
            self.reload(None)

    # --- updates ---

    def _update_loop(self) -> None:
        """Look for a new release a minute after launch, then every 6 hours."""
        time.sleep(60)
        while True:
            self._look_for_update(announce=True)
            time.sleep(6 * 3600)

    def _look_for_update(self, announce: bool) -> None:
        try:
            update = updater.check_for_update(__version__)
        except Exception:
            log.warning("Couldn't check for updates", exc_info=True)
            return
        if update and (not self.available_update or update.version != self.available_update.version):
            log.info("Update available: v%s", update.version)
            self.available_update = update
            if announce:
                self._update_prompt = update

    def check_for_updates(self, _item) -> None:
        if self.available_update:
            self._start_update()
            return

        def check() -> None:
            self.update_status = "Checking for updates…"
            self._look_for_update(announce=False)
            self.update_status = None
            if self.available_update:
                self._update_prompt = self.available_update
            else:
                self._alerts.append(("You're up to date", f"Speech to Text {__version__} is the latest version."))

        threading.Thread(target=check, daemon=True).start()

    def _start_update(self) -> None:
        update, app_path = self.available_update, updater.running_app_path()
        if not update or not app_path or self.update_status:
            return

        def run() -> None:
            try:
                with tempfile.TemporaryDirectory(prefix="stt-download-") as folder:
                    def progress(done: int, total: int) -> None:
                        percent = f" {done * 100 // total}%" if total else ""
                        self.update_status = f"Downloading v{update.version}…{percent}"

                    dmg = updater.download(update, Path(folder), progress)
                    self.update_status = f"Installing v{update.version}…"
                    updater.install_from_dmg(dmg, app_path)
                self.update_status = f"Restarting into v{update.version}…"
                self._update_ready_to_relaunch = True
            except Exception as exc:
                log.exception("Update failed")
                self.update_status = None
                self._alerts.append((
                    "The update didn't work",
                    f"{exc}\n\nYou can download it yourself from the release page, which will open now.",
                ))
                subprocess.Popen(["open", update.notes_url or f"https://github.com/{updater.REPO}/releases/latest"])

        threading.Thread(target=run, daemon=True).start()

    def _refresh_update_ui(self) -> None:
        if self._update_prompt:
            update, self._update_prompt = self._update_prompt, None
            if rumps.alert(
                f"Speech to Text {update.version} is available",
                f"You have {__version__}. Update now? It downloads in the background (~150 MB) and the app "
                "restarts by itself. Your history, settings and permissions stay as they are.",
                ok="Update",
                cancel="Later",
            ) == 1:
                self._start_update()
        idle = not self.recorder.is_recording and not self.jobs.unfinished_tasks and not self.meetings.busy
        if self._update_ready_to_relaunch and idle:
            self._update_ready_to_relaunch = False
            app_path = updater.running_app_path()
            if app_path:
                updater.relaunch(app_path)
            rumps.quit_application()
        if self.update_status:
            title, callback = self.update_status, None
        elif self.available_update:
            title, callback = f"⬆️ Update to v{self.available_update.version}", self.check_for_updates
        else:
            title, callback = f"Check for updates (v{__version__})", self.check_for_updates
        if self.update_item.title != title:
            self.update_item.title = title
            self.update_item.set_callback(callback)

    def _refresh(self, _timer) -> None:
        """Runs 4x a second on the main thread. Each part is isolated, so one failing (and logging)
        can never stop the icon and menu from updating."""
        self._ticks += 1
        sections = [self._refresh_meeting_ui, self._show_pending_alerts, self._refresh_menu_items,
                    self._refresh_status]
        if FROZEN:
            sections.insert(1, self._refresh_update_ui)
        if self._ticks % 8 == 1:  # every 2 seconds
            sections.insert(0, self._check_permissions)
        for section in sections:
            try:
                section()
            except Exception as exc:
                key = f"{section.__name__}: {exc}"
                if key not in self._refresh_errors:  # log each distinct problem once
                    self._refresh_errors.add(key)
                    log.exception("Menu bar refresh failed in %s", section.__name__)

    def _show_pending_alerts(self) -> None:
        if self._alerts:
            title, message = self._alerts.pop(0)
            rumps.alert(title, message)
        if self._restart_suggestion:
            title, message = self._restart_suggestion
            self._restart_suggestion = None
            if rumps.alert(title, message, ok="Restart now", cancel="Later") == 1:
                self.reload(None)
        if self._paste_help_pending and not self._paste_help_shown:
            self._paste_help_pending, self._paste_help_shown = False, True
            if rumps.alert(
                "Allow Accessibility to paste automatically",
                "Your text was transcribed and copied: press ⌘V to paste it.\n\n"
                "To have it pasted for you, turn on Speech to Text under Accessibility in System Settings.",
                ok="Open Settings",
                cancel="Later",
            ) == 1:
                permissions.open_settings(permissions.ACCESSIBILITY)

    def _refresh_menu_items(self) -> None:
        setup_title = CLEANUP_SETUP_TITLES.get(self.cleanup_state, self.cleanup_state)
        if self.setup_item.title != setup_title:
            self.setup_item.title = setup_title
            self.setup_item.set_callback(self.open_ollama_download if self.cleanup_state == "no_ollama" else None)
        if self._menu_stale:
            self._menu_stale = False
            self._rebuild_recent_menu()

    def _refresh_status(self) -> None:
        hotkey = self.config.hotkey.replace("_", " ")
        if self.recorder.is_recording:
            elapsed = int(time.monotonic() - self.recording_started)
            title, status = f"{ICON_RECORDING} {elapsed // 60}:{elapsed % 60:02d}", f"Recording… tap {hotkey} to stop"
        elif self.jobs.unfinished_tasks and self.model_ready:
            title, status = ICON_WORKING, "Transcribing…"
        elif self.meetings.recording:
            elapsed = int(self.meetings.recorder.elapsed)
            title = f"{ICON_MEETING} {elapsed // 60}:{elapsed % 60:02d}"
            status = "Recording meeting notes (dictation still works)"
        elif self.meetings.progress:
            title, status = ICON_WORKING, self.meetings.progress
        elif not self.model_ready and self.download_progress and self.download_progress[1]:
            done, total = self.download_progress
            percent = min(99, done * 100 // total)
            title = f"{ICON_LOADING} {percent}%"
            status = f"Downloading the speech model: {done / 1e9:.1f} of {total / 1e9:.1f} GB (one time only)"
        elif self.missing_permissions:
            title, status = ICON_ERROR, f"Needs permission: {', '.join(self.missing_permissions)} (see below)"
        elif not self.model_ready:
            title, status = ICON_LOADING, "Loading the speech model… (dictation starts working when it's ready)"
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

    # --- recovering from freezes ---

    def _recover_from_freeze(self, frozen_seconds: float) -> None:
        """Runs on the watchdog thread when the main thread stopped responding: keep what can be kept,
        leave a note for the next launch, and restart."""
        import faulthandler

        log.error("Frozen for %.0fs: saving any recording and restarting. Where every thread was:", frozen_seconds)
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)  # the console log, when packaged
        rescued = None
        if self.recorder.is_recording:
            try:
                rescued = self.pipeline.rescue(self.recorder.snapshot(), "The app froze while you were recording")
            except Exception:
                log.exception("Couldn't save the recording in progress")
        note = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "frozen_seconds": round(frozen_seconds),
                "rescued_dictation": rescued, "meeting": self.meetings.current_id}
        try:
            (DATA_DIR / FREEZE_MARKER).write_text(json.dumps(note))
        except OSError:
            log.exception("Couldn't write the freeze note")
        for handler in logging.getLogger().handlers:
            handler.flush()
        app_path = updater.running_app_path()
        if app_path:
            updater.relaunch(app_path)
        else:
            subprocess.Popen([sys.executable, "-m", "speech_to_text"], start_new_session=True)
        os._exit(3)

    def _announce_previous_freeze(self) -> None:
        marker = DATA_DIR / FREEZE_MARKER
        if not marker.exists():
            return
        try:
            note = json.loads(marker.read_text())
        except (OSError, ValueError):
            note = {}
        marker.unlink(missing_ok=True)
        parts = ["Speech to Text stopped responding, so it restarted itself."]
        if note.get("rescued_dictation"):
            parts.append("What you were dictating was saved: it's under Recent with ❌. Click it to transcribe it.")
        if note.get("meeting"):
            parts.append("The meeting you were recording is being transcribed from everything captured until then.")
        parts.append("Details are in the log (Advanced → Show log files in Finder), if you want to report it.")
        self._alerts.append(("Speech to Text recovered from a freeze", "\n\n".join(parts)))

    # --- meeting notes ---

    def toggle_meeting(self, _item=None) -> None:
        # Starting talks to ScreenCaptureKit and waits for it: never on the main thread.
        action = self._stop_meeting if self.meetings.recording else self._start_meeting
        threading.Thread(target=action, name="meeting-toggle", daemon=True).start()

    def _start_meeting(self) -> dict:
        try:
            result = self.meetings.start()
        except Exception as exc:
            log.exception("Couldn't start the meeting recording")
            self._alerts.append(("Couldn't start the meeting notes", str(exc)))
            if self.config.sounds:
                play_sound("Basso")
            return {"message": f"Couldn't start: {exc}"}
        if self.config.sounds:
            play_sound("Tink")
        if result.get("system_audio") is False and not self._meeting_hint_shown:
            self._meeting_hint_shown = True
            self._alerts.append((
                "Recording your microphone only",
                "To also record the other people (the computer's audio), allow Speech to Text under "
                "Screen & System Audio Recording in System Settings → Privacy & Security, then restart the app. "
                "Nothing of your screen is recorded: only the sound.",
            ))
            permissions.open_settings(permissions.SCREEN_RECORDING)
        return result

    def _stop_meeting(self) -> dict:
        result = self.meetings.stop()
        if result and self.config.sounds:
            play_sound("Pop")
        return result

    def open_window(self, meeting_id: int | None = None) -> None:
        url = self.webui.start()
        self.window.show(url + (f"&meeting={meeting_id}" if meeting_id else ""))

    def _refresh_meeting_ui(self) -> None:
        if self.meetings.recording:
            elapsed = int(self.meetings.recorder.elapsed)
            title = f"⏹ Stop meeting notes ({elapsed // 60}:{elapsed % 60:02d})"
        else:
            title = f"{ICON_MEETING} Start meeting notes"
        if self.meeting_item.title != title:
            self.meeting_item.title = title
        if self._notes_ready:
            meeting_id = self._notes_ready.pop(0)
            meeting = self.meetings.store.get(meeting_id)
            name = meeting.display_title if meeting else "Your meeting"
            if rumps.alert("Meeting notes ready", f"“{name}” is transcribed and summarized.",
                           ok="Open notes", cancel="Later") == 1:
                self.open_window(meeting_id)

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
