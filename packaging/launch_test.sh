#!/usr/bin/env bash
# Opens the built app like a user would (first run, no permissions granted) and checks it stays up.
# Prints the app log and any crash report, so a crash on launch fails the build with the reason.
set -uo pipefail
cd "$(dirname "$0")/.."
APP="$PWD/dist/Speech to Text.app"
rm -rf ~/.config/speech-to-text ~/Library/Logs/speech-to-text.log
before=$(ls ~/Library/Logs/DiagnosticReports 2>/dev/null | sort)

open "$APP"
sleep 45
PID=$(pgrep -f "Speech to Text.app/Contents/MacOS/Speech to Text" | head -1)

echo "=== app log ==="
cat ~/Library/Logs/speech-to-text.log 2>/dev/null || echo "(no log written)"
echo "=== new crash reports ==="
for report in $(comm -13 <(echo "$before") <(ls ~/Library/Logs/DiagnosticReports 2>/dev/null | sort)); do
  echo "--- $report"
  head -c 20000 ~/Library/Logs/DiagnosticReports/"$report"
done

if [[ -z "$PID" ]]; then
  echo "launch test: the app is NOT running 45s after opening it" >&2
  exit 1
fi
echo "launch test: app still running (pid $PID)"
kill "$PID"
