"""A native window showing the web UI (webui.py) in a WKWebView.

While it's open the app shows in the Dock and the ⌘-Tab switcher like a regular app, and gets an
Edit menu so ⌘C / ⌘V / ⌘A work in it; when it closes, the app goes back to living in the menu
bar only. Falls back to the default browser if WebKit isn't available.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

_delegate_class = None


def _window_delegate_class():
    global _delegate_class
    if _delegate_class is None:
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory
        from Foundation import NSObject

        class SpeechToTextWindowDelegate(NSObject):
            def windowWillClose_(self, notification):
                NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        _delegate_class = SpeechToTextWindowDelegate
    return _delegate_class


class AppWindow:
    def __init__(self, title: str = "Speech to Text"):
        self.title = title
        self._window = None
        self._webview = None
        self._delegate = None
        self._loaded_url: str | None = None

    def show(self, url: str) -> None:
        """Open (or bring to the front) the window at url. Call on the main thread."""
        try:
            self._show(url)
        except Exception:
            log.exception("Couldn't open the app window; using the browser instead")
            subprocess.Popen(["open", url])

    def _show(self, url: str) -> None:
        from AppKit import (
            NSApp,
            NSApplicationActivationPolicyRegular,
            NSBackingStoreBuffered,
            NSMakeRect,
            NSWindow,
            NSWindowStyleMaskClosable,
            NSWindowStyleMaskMiniaturizable,
            NSWindowStyleMaskResizable,
            NSWindowStyleMaskTitled,
        )
        from Foundation import NSURL, NSURLRequest
        from WebKit import WKWebView, WKWebViewConfiguration

        if self._window is None:
            style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
                     | NSWindowStyleMaskMiniaturizable)
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 1120, 740), style, NSBackingStoreBuffered, False
            )
            window.setTitle_(self.title)
            window.setReleasedWhenClosed_(False)
            window.setMinSize_((720, 460))
            window.center()
            window.setFrameAutosaveName_("SpeechToTextMainWindow")
            webview = WKWebView.alloc().initWithFrame_configuration_(
                window.contentView().bounds(), WKWebViewConfiguration.alloc().init()
            )
            webview.setAutoresizingMask_(2 | 16)  # width and height follow the window
            window.setContentView_(webview)
            self._delegate = _window_delegate_class().alloc().init()
            window.setDelegate_(self._delegate)
            self._window, self._webview = window, webview
            _install_edit_menu()

        if url != self._loaded_url:
            self._webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
            self._loaded_url = url
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)


def _install_edit_menu() -> None:
    """Menu-bar-only apps have no main menu, and without one ⌘C/⌘V don't reach the web view."""
    from AppKit import NSApp, NSMenu, NSMenuItem

    main = NSMenu.alloc().init()
    app_item = NSMenuItem.alloc().init()
    main.addItem_(app_item)
    app_menu = NSMenu.alloc().init()
    app_menu.addItemWithTitle_action_keyEquivalent_("Close Window", "performClose:", "w")
    app_item.setSubmenu_(app_menu)

    edit_item = NSMenuItem.alloc().init()
    main.addItem_(edit_item)
    edit = NSMenu.alloc().initWithTitle_("Edit")
    for entry in [
        ("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), None,
        ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a"),
    ]:
        if entry is None:
            edit.addItem_(NSMenuItem.separatorItem())
        else:
            edit.addItemWithTitle_action_keyEquivalent_(*entry)
    edit_item.setSubmenu_(edit)
    NSApp.setMainMenu_(main)
