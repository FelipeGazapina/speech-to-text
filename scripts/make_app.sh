#!/usr/bin/env bash
# Wraps the installed `stt` command in a tiny menu-bar-only .app so it can
# live in Login Items and get its own macOS permissions (instead of Terminal's).
set -euo pipefail

STT_BIN="$(command -v stt || true)"
if [[ -z "$STT_BIN" ]]; then
  echo "The 'stt' command isn't on your PATH. Install it first: uv tool install ." >&2
  exit 1
fi

APP="$HOME/Applications/Speech to Text.app"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Speech to Text</string>
  <key>CFBundleIdentifier</key><string>local.speech-to-text</string>
  <key>CFBundleExecutable</key><string>speech-to-text</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Records your voice while dictation is on, to transcribe it locally.</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/speech-to-text" <<LAUNCHER
#!/bin/bash
exec "$STT_BIN"
LAUNCHER
chmod +x "$APP/Contents/MacOS/speech-to-text"

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true

echo "Created: $APP"
echo
echo "Next:"
echo "  1. Quit any 'stt' running in Terminal, then open the app:  open \"$APP\""
echo "  2. Allow Microphone, Input Monitoring and Accessibility for 'Speech to Text' when macOS asks"
echo "     (System Settings -> Privacy & Security), then use 'Reload config' from its menu."
echo "  3. Start at login: System Settings -> General -> Login Items -> + -> Speech to Text"
