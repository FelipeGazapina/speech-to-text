"""The three macOS privacy permissions the app needs, checked for real.

macOS lets an app install its keyboard listener even without Input Monitoring; it just never
delivers any keys. So "the listener was created" says nothing: we ask macOS directly.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

MICROPHONE, ACCESSIBILITY, INPUT_MONITORING = "Microphone", "Accessibility", "Input Monitoring"

SCREEN_RECORDING = "Screen & System Audio Recording"  # only for meeting notes, so not in missing_permissions()

_SETTINGS_PANES = {
    SCREEN_RECORDING: "Privacy_ScreenCapture",
    MICROPHONE: "Privacy_Microphone",
    ACCESSIBILITY: "Privacy_Accessibility",
    INPUT_MONITORING: "Privacy_ListenEvent",
}

WHY = {
    MICROPHONE: "to hear you",
    ACCESSIBILITY: "to paste the text (⌘V) where your cursor is",
    INPUT_MONITORING: "to notice when you tap the hotkey",
}


def missing_permissions() -> list[str]:
    """Permissions not granted yet, in the order worth fixing them."""
    missing = []
    if not _input_monitoring_granted():
        missing.append(INPUT_MONITORING)
    if not _accessibility_granted():
        missing.append(ACCESSIBILITY)
    if _microphone_denied():
        missing.append(MICROPHONE)
    return missing


def request_all() -> None:
    """Show macOS's own permission prompts (each one appears only the first time it's asked)."""
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
    except Exception:
        log.exception("Input Monitoring request failed")
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
        log.exception("Accessibility request failed")
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

        if AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio) == 0:  # not asked yet
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda granted: None)
    except Exception:
        log.exception("Microphone request failed")


def open_settings(permission: str) -> None:
    pane = _SETTINGS_PANES[permission]
    subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{pane}"])


def _input_monitoring_granted() -> bool:
    try:
        import Quartz

        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        return True  # can't tell; don't nag


def _accessibility_granted() -> bool:
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return True


def _microphone_denied() -> bool:
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

        return AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio) in (1, 2)  # restricted, denied
    except Exception:
        return False
