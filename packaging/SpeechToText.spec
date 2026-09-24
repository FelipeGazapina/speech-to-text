# PyInstaller recipe for "Speech to Text.app". Build it with packaging/build_dmg.sh on an Apple Silicon Mac.
import os

from PyInstaller.utils.hooks import collect_all

import speech_to_text

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas, binaries, hiddenimports = [], [], []
# Packages with native libraries or data files PyInstaller can't discover on its own:
# MLX (libmlx + Metal shaders), Whisper's mel filters and tokenizer vocabularies, PortAudio.
for package in ("mlx", "mlx_whisper", "sounddevice", "_sounddevice_data", "tiktoken", "tiktoken_ext"):
    try:
        package_datas, package_binaries, package_imports = collect_all(package)
    except Exception:
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # torch is only used by mlx_whisper's model-conversion code; leaving it out saves ~1 GB.
    excludes=["torch", "torchvision", "torchaudio", "tkinter", "matplotlib", "IPython", "pytest",
              "faster_whisper", "ctranslate2"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Speech to Text",
    console=False,
    target_arch="arm64",
)
coll = COLLECT(exe, a.binaries, a.datas, name="Speech to Text")
app = BUNDLE(
    coll,
    name="Speech to Text.app",
    icon=os.path.join(ROOT, "build", "icon.icns"),
    bundle_identifier="io.github.felipegazapina.speech-to-text",
    version=speech_to_text.__version__,
    info_plist={
        "CFBundleDisplayName": "Speech to Text",
        "CFBundleShortVersionString": speech_to_text.__version__,
        "LSUIElement": True,  # menu bar only, no Dock icon
        "LSMinimumSystemVersion": "14.0",
        "NSMicrophoneUsageDescription": "Speech to Text records your voice while you dictate and transcribes it on this Mac.",
        "NSHighResolutionCapable": True,
    },
)
