"""Global hotkey via a listen-only Quartz event tap on the main run loop.

A modifier hotkey (e.g. right Option) fires only when tapped alone: if any other
key is pressed while it's held, it was part of a shortcut and is ignored.
Requires Input Monitoring permission (System Settings -> Privacy & Security).
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

# name -> (virtual keycode, device-specific flag bit that is set while the key is down)
MODIFIER_KEYS = {
    "right_option": (61, 0x00000040),
    "left_option": (58, 0x00000020),
    "right_command": (54, 0x00000010),
    "left_command": (55, 0x00000008),
    "right_control": (62, 0x00002000),
    "left_control": (59, 0x00000001),
    "right_shift": (60, 0x00000004),
    "left_shift": (56, 0x00000002),
    "fn": (63, 0x00800000),
}

# F13-F19 don't exist on Mac keyboards, which makes them ideal remap targets.
FUNCTION_KEYS = {"f13": 105, "f14": 107, "f15": 113, "f16": 106, "f17": 64, "f18": 79, "f19": 80}

ESC_KEYCODE = 53


def validate_hotkey(name: str) -> str:
    name = name.strip().lower()
    if name not in MODIFIER_KEYS and name not in FUNCTION_KEYS:
        options = ", ".join([*MODIFIER_KEYS, *FUNCTION_KEYS])
        raise ValueError(f"Unknown hotkey {name!r}. Use one of: {options}")
    return name


class TapDetector:
    """Pure key-event state machine, kept separate from Quartz so it can be tested."""

    def __init__(self, hotkey: str):
        self.hotkey = validate_hotkey(hotkey)
        self._is_modifier = self.hotkey in MODIFIER_KEYS
        self._keycode, self._mask = MODIFIER_KEYS.get(self.hotkey, (FUNCTION_KEYS.get(self.hotkey), 0))
        self._pending = False

    def key_down(self, keycode: int, autorepeat: bool) -> bool:
        """Returns True if this event is the hotkey firing."""
        if self._is_modifier:
            self._pending = False  # another key while the modifier is held: it's a shortcut
            return False
        return keycode == self._keycode and not autorepeat

    def flags_changed(self, keycode: int, flags: int) -> bool:
        if not self._is_modifier:
            return False
        if keycode != self._keycode:
            self._pending = False  # another modifier joined in: it's a shortcut
            return False
        if flags & self._mask:
            self._pending = True
            return False
        fired, self._pending = self._pending, False
        return fired


class HotkeyListener:
    def __init__(self, hotkey: str, on_toggle: Callable[[], None], on_escape: Callable[[], None] | None = None):
        self.detector = TapDetector(hotkey)
        self.on_toggle = on_toggle
        self.on_escape = on_escape
        self._tap = None
        self._callback = None  # keep a reference so PyObjC doesn't free it

    def start(self) -> bool:
        """Install the event tap. Returns False if macOS denied it (missing permission)."""
        import CoreFoundation as CF
        import Quartz as Q

        def callback(proxy, event_type, event, refcon):
            try:
                if event_type in (Q.kCGEventTapDisabledByTimeout, Q.kCGEventTapDisabledByUserInput):
                    Q.CGEventTapEnable(self._tap, True)
                    return event
                keycode = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
                if event_type == Q.kCGEventKeyDown:
                    autorepeat = bool(Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventAutorepeat))
                    if keycode == ESC_KEYCODE and not autorepeat and self.on_escape:
                        self.on_escape()
                    if self.detector.key_down(keycode, autorepeat):
                        self.on_toggle()
                elif event_type == Q.kCGEventFlagsChanged:
                    if self.detector.flags_changed(keycode, Q.CGEventGetFlags(event)):
                        self.on_toggle()
            except Exception:
                log.exception("Hotkey callback failed")
            return event

        self._callback = callback
        mask = Q.CGEventMaskBit(Q.kCGEventKeyDown) | Q.CGEventMaskBit(Q.kCGEventFlagsChanged)
        self._tap = Q.CGEventTapCreate(
            Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap, Q.kCGEventTapOptionListenOnly, mask, callback, None
        )
        if self._tap is None:
            return False
        source = CF.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        CF.CFRunLoopAddSource(CF.CFRunLoopGetMain(), source, CF.kCFRunLoopCommonModes)
        Q.CGEventTapEnable(self._tap, True)
        log.info("Listening for hotkey %r", self.detector.hotkey)
        return True
