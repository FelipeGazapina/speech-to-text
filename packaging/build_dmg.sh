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
# Speak two sentences with macOS voices and transcribe them inside the packaged app.
say -o build/en.aiff "Please open a pull request on GitHub and run the tests."
afconvert -f WAVE -d LEI16@16000 -c 1 build/en.aiff build/en.wav
if say -v Luciana -o build/pt.aiff "Abre um pull request no GitHub e roda os testes, por favor." 2>/dev/null; then
  afconvert -f WAVE -d LEI16@16000 -c 1 build/pt.aiff build/pt.wav
fi
"$APP/Contents/MacOS/Speech to Text" --self-test --self-test-model tiny --self-test-audio build/*.wav

mkdir -p build/dmg
cp -R "$APP" build/dmg/
ln -s /Applications build/dmg/Applications
hdiutil create -volname "Speech to Text" -srcfolder build/dmg -ov -format UDZO dist/SpeechToText.dmg
echo "Built dist/SpeechToText.dmg ($(du -h dist/SpeechToText.dmg | cut -f1))"
