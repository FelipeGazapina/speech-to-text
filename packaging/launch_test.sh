#!/usr/bin/env bash
# Opens the built app like a user would (first run), waits for the model to load, taps the hotkey
# twice (start + stop recording) and checks the app is still running. Prints the app's logs and any
# crash report, so a crash fails the build with the reason.
set -uo pipefail
cd "$(dirname "$0")/.."
APP="$PWD/dist/Speech to Text.app"
LOG=~/Library/Logs/speech-to-text.log
rm -rf ~/.config/speech-to-text "$LOG" ~/Library/Logs/speech-to-text-console.log
before=$(ls ~/Library/Logs/DiagnosticReports 2>/dev/null | sort)
app_pid() { pgrep -f "Speech to Text.app/Contents/MacOS/Speech to Text" | head -1; }

open "$APP"
for _ in $(seq 1 84); do  # up to 7 minutes: the first run downloads the 1.6 GB model
  sleep 5
  grep -qE "Model ready|Failed to load" "$LOG" 2>/dev/null && break
  [[ -z "$(app_pid)" ]] && break
done
if [[ -n "$(app_pid)" ]]; then
  python3 packaging/tap_hotkey.py 2
  sleep 20
fi
PID=$(app_pid)

echo "=== app log ==="
grep -v "httpx" "$LOG" 2>/dev/null || echo "(no log written)"
echo "=== console log ==="
cat ~/Library/Logs/speech-to-text-console.log 2>/dev/null || echo "(none)"
echo "=== new crash reports ==="
for report in $(comm -13 <(echo "$before") <(ls ~/Library/Logs/DiagnosticReports 2>/dev/null | sort)); do
  echo "--- $report"
  head -c 30000 ~/Library/Logs/DiagnosticReports/"$report"
done

if [[ -z "$PID" ]]; then
  echo "launch test: the app is NOT running anymore" >&2
  exit 1
fi
echo "launch test: app still running (pid $PID)"
kill "$PID"
