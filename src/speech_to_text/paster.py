"""Put text wherever the cursor is: clipboard + synthetic Cmd+V, then restore the clipboard.

Requires Accessibility permission (System Settings -> Privacy & Security).
"""

from __future__ import annotations

import time

_V_KEYCODE = 9


def paste_text(text: str, restore_clipboard: bool = True) -> None:
    from AppKit import NSPasteboard, NSPasteboardTypeString

    pasteboard = NSPasteboard.generalPasteboard()
    saved = _snapshot(pasteboard) if restore_clipboard else None

    pasteboard.clearContents()
    pasteboard.setString_forType_(text, NSPasteboardTypeString)
    our_change = pasteboard.changeCount()

    time.sleep(0.05)
    _press_cmd_v()

    if saved is not None:
        # Give the target app time to read the clipboard before putting the old contents back.
        time.sleep(0.4)
        if pasteboard.changeCount() == our_change:  # don't clobber something the user copied meanwhile
            _restore(pasteboard, saved)


def copy_text(text: str) -> None:
    from AppKit import NSPasteboard, NSPasteboardTypeString

    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    pasteboard.setString_forType_(text, NSPasteboardTypeString)


def _press_cmd_v() -> None:
    import Quartz as Q

    source = Q.CGEventSourceCreate(Q.kCGEventSourceStateHIDSystemState)
    for is_down in (True, False):
        event = Q.CGEventCreateKeyboardEvent(source, _V_KEYCODE, is_down)
        Q.CGEventSetFlags(event, Q.kCGEventFlagMaskCommand)
        Q.CGEventPost(Q.kCGHIDEventTap, event)


def _snapshot(pasteboard) -> list[dict]:
    items = []
    for item in pasteboard.pasteboardItems() or []:
        data = {}
        for kind in item.types():
            value = item.dataForType_(kind)
            if value is not None:
                data[kind] = value
        items.append(data)
    return items


def _restore(pasteboard, items: list[dict]) -> None:
    from AppKit import NSPasteboardItem

    pasteboard.clearContents()
    restored = []
    for data in items:
        item = NSPasteboardItem.alloc().init()
        for kind, value in data.items():
            item.setData_forType_(value, kind)
        restored.append(item)
    if restored:
        pasteboard.writeObjects_(restored)
