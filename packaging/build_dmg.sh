#!/usr/bin/env bash
# Builds dist/SpeechToText.dmg ("Speech to Text.app" + a shortcut to Applications).
# Needs an Apple Silicon Mac with Python 3.11+. GitHub Actions runs this on every push.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m pip install --upgrade pip
python3 -m pip install . pyinstaller pillow

rm -rf build dist
python3 packaging/make_icon.py build/icon.iconset
iconutil -c icns build/icon.iconset -o build/icon.icns

python3 -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/SpeechToText.spec

APP="dist/Speech to Text.app"
# Ad-hoc signature: required for Apple Silicon to run it at all. (Not notarized: see README.)
codesign --force --deep --sign - "$APP"
"$APP/Contents/MacOS/Speech to Text" --self-test

mkdir -p build/dmg
cp -R "$APP" build/dmg/
ln -s /Applications build/dmg/Applications
hdiutil create -volname "Speech to Text" -srcfolder build/dmg -ov -format UDZO dist/SpeechToText.dmg
echo "Built dist/SpeechToText.dmg ($(du -h dist/SpeechToText.dmg | cut -f1))"
