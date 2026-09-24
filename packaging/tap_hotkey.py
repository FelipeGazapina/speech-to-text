"""Simulates tapping the right Option key (CI only): down, then up, like a person would."""

import sys
import time

import Quartz as Q

RIGHT_OPTION = 61
PRESSED = Q.kCGEventFlagMaskAlternate | 0x40  # 0x40: the device bit for the *right* Option key
RELEASED = 0x100  # "no modifiers" as macOS reports it


def post_flags(flags: int) -> None:
    event = Q.CGEventCreateKeyboardEvent(None, RIGHT_OPTION, True)
    Q.CGEventSetType(event, Q.kCGEventFlagsChanged)
    Q.CGEventSetFlags(event, flags)
    Q.CGEventPost(Q.kCGHIDEventTap, event)


def tap() -> None:
    post_flags(PRESSED)
    time.sleep(0.1)
    post_flags(RELEASED)


if __name__ == "__main__":
    for i in range(int(sys.argv[1]) if len(sys.argv) > 1 else 1):
        if i:
            time.sleep(3)
        tap()
        print("tapped right Option", flush=True)
