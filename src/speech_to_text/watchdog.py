"""Guards against the app hanging: deadlines for calls that can block forever, and a watchdog that
notices when the main thread (menu bar, hotkey) stops responding."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class Timeout(TimeoutError):
    pass


def run_with_timeout(fn: Callable[[], T], seconds: float, what: str) -> T:
    """Run fn on a helper thread and wait at most `seconds`. On timeout the helper thread is
    abandoned (it can't be killed) and Timeout is raised, so the caller never hangs."""
    outcome: dict = {}
    finished = threading.Event()

    def call() -> None:
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # handed back to the caller
            outcome["error"] = exc
        finished.set()

    threading.Thread(target=call, name=f"deadline:{what}", daemon=True).start()
    if not finished.wait(seconds):
        log.error("%s didn't finish within %.0fs; giving up on it", what, seconds)
        raise Timeout(f"{what} didn't respond within {seconds:.0f} seconds")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


class MainThreadWatchdog:
    """The main thread calls beat() about once a second (from a timer that also runs while menus and
    alerts are open). If the beats stop for `limit` seconds, on_freeze runs, once, on this thread."""

    def __init__(self, on_freeze: Callable[[float], None], limit: float = 30.0, check_every: float = 2.0):
        self.on_freeze = on_freeze
        self.limit = limit
        self.check_every = check_every
        self.last_beat = time.monotonic()
        self.fired = False
        self._stop = threading.Event()

    def beat(self, *_args) -> None:
        self.last_beat = time.monotonic()

    def start(self) -> "MainThreadWatchdog":
        threading.Thread(target=self._watch, name="watchdog", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _watch(self) -> None:
        while not self._stop.wait(self.check_every):
            silent = time.monotonic() - self.last_beat
            if silent > self.limit and not self.fired:
                self.fired = True
                log.error("The app has been frozen for %.0fs", silent)
                try:
                    self.on_freeze(silent)
                except Exception:
                    log.exception("Recovering from the freeze failed")
