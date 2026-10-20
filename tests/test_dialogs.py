"""Messages from the menu bar app never block it: the menu, the meeting timer and Stop keep working
while a message waits to be read."""

import pytest

AppKit = pytest.importorskip("AppKit")

from speech_to_text import dialogs  # noqa: E402


def test_a_message_waits_on_screen_without_blocking_and_runs_its_button():
    AppKit.NSApplication.sharedApplication()
    clicked = []
    alert = dialogs.show("Meeting notes ready", "“Standup” is transcribed.", ok="Open notes", cancel="Later",
                         on_ok=lambda: clicked.append("open"))

    assert alert.window().isVisible()
    assert [b.title() for b in alert.buttons()] == ["Open notes", "Later"]

    alert.buttons()[0].performClick_(None)
    assert clicked == ["open"] and not alert.window().isVisible()


def test_later_closes_the_message_without_running_the_action():
    AppKit.NSApplication.sharedApplication()
    clicked = []
    alert = dialogs.show("Update available", "Update now?", ok="Update", cancel="Later",
                         on_ok=lambda: clicked.append("update"))
    alert.buttons()[1].performClick_(None)
    assert clicked == [] and not alert.window().isVisible()
