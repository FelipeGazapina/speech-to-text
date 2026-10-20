"""Messages that wait on screen without blocking the app.

rumps.alert() runs a modal loop: until it's answered, menu bar timers stop and every menu item is
disabled, including Stop meeting notes. A menu bar app's alert also easily ends up behind other
windows, so it can sit unseen for an hour. These are ordinary floating windows instead.
Call on the main thread.
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

_responder_class = None
_open: dict[int, tuple] = {}  # keeps each alert and its responder alive until it's answered


def _responder():
    global _responder_class
    if _responder_class is None:
        from Foundation import NSObject

        class SpeechToTextDialogResponder(NSObject):
            def clicked_(self, button):
                _open.pop(id(self), None)
                self.alert.window().orderOut_(None)
                if button is self.alert.buttons()[0] and self.on_ok is not None:
                    try:
                        self.on_ok()
                    except Exception:
                        log.exception("A dialog's action failed")

        _responder_class = SpeechToTextDialogResponder
    return _responder_class


def show(title: str, message: str, ok: str = "OK", cancel: str | None = None,
         on_ok: Callable[[], None] | None = None):
    """Show a message and return right away; on_ok runs if the first button is clicked."""
    from AppKit import NSAlert, NSApp, NSFloatingWindowLevel

    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.addButtonWithTitle_(ok)
    if cancel:
        alert.addButtonWithTitle_(cancel).setKeyEquivalent_("\x1b")
    responder = _responder().alloc().init()
    responder.alert, responder.on_ok = alert, on_ok
    for button in alert.buttons():
        button.setTarget_(responder)
        button.setAction_("clicked:")
    _open[id(responder)] = (alert, responder)

    alert.layout()
    window = alert.window()
    window.setLevel_(NSFloatingWindowLevel)
    window.center()
    NSApp.activateIgnoringOtherApps_(True)
    window.makeKeyAndOrderFront_(None)
    return alert
