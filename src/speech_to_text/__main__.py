from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import CONFIG_PATH, load_config
from .history import STATUSES
from .hotkey import validate_hotkey

LOG_PATH = Path.home() / "Library" / "Logs" / "speech-to-text.log"
# Everything that isn't a normal log line: Python errors printed by libraries, and native crash traces.
CONSOLE_LOG_PATH = LOG_PATH.with_name("speech-to-text-console.log")


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=2),
        logging.StreamHandler(),
    ]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def capture_console_output() -> None:
    """Opened from Finder, the app's console output goes nowhere. Keep it, plus native crash traces, in a file."""
    import faulthandler
    import threading

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    CONSOLE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    console = open(CONSOLE_LOG_PATH, "a", buffering=1, encoding="utf-8")
    console.write(f"\n===== started {__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    sys.stdout = sys.stderr = console
    faulthandler.enable(file=console, all_threads=True)  # a hard crash still leaves a Python traceback

    def log_thread_crash(hook_args) -> None:
        logging.getLogger("speech_to_text").error(
            "Uncaught error in thread %s", hook_args.thread.name if hook_args.thread else "?",
            exc_info=(hook_args.exc_type, hook_args.exc_value, hook_args.exc_traceback),
        )

    threading.excepthook = log_thread_crash


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stt", description="Tap a key, talk, tap again: text appears at your cursor.")
    parser.add_argument("--config-path", action="store_true", help="print the config file location and exit")
    # Used by the app build: check the bundle, optionally transcribing real speech with a small model.
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test-model", help=argparse.SUPPRESS)
    parser.add_argument("--self-test-audio", nargs="*", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--self-test-meeting", nargs=2, metavar=("ME_WAV", "OTHERS_WAV"), help=argparse.SUPPRESS)
    # Used by the app build: install a .dmg over a copy of the app, exactly like an in-app update.
    parser.add_argument("--self-test-update", nargs=2, metavar=("DMG", "APP"), help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    history = commands.add_parser("history", help="list past dictations (newest first)")
    history.add_argument("search", nargs="?", help="only show dictations containing this text")
    history.add_argument("-n", "--limit", type=int, default=20)
    history.add_argument("--lang", help="only this language, e.g. pt or en")
    history.add_argument("--raw", action="store_true", help="also show what Whisper heard before cleanup")
    history.add_argument("--status", choices=STATUSES, help="only dictations with this status")

    retry = commands.add_parser("retry", help="re-transcribe dictations whose transcription failed (audio was kept)")
    retry.add_argument("id", nargs="?", type=int, help="default: every failed dictation")

    copy = commands.add_parser("copy", help="copy a past dictation to the clipboard (default: the last one)")
    copy.add_argument("id", nargs="?", type=int)

    learn = commands.add_parser("learn", help='teach a fix, e.g. stt learn "get hub" "GitHub"')
    learn.add_argument("wrong")
    learn.add_argument("right")

    unlearn = commands.add_parser("unlearn", help="forget a learned fix")
    unlearn.add_argument("wrong")

    commands.add_parser("profile", help="show what the app has learned about how you talk")
    return parser


def main(argv: list[str] | None = None) -> None:
    if getattr(sys, "frozen", False) and not any(a.startswith("--self-test") for a in (argv or sys.argv)):
        capture_console_output()
    # parse_known_args: macOS can pass extra launch arguments when the app is opened from Finder.
    args, _unknown = build_parser().parse_known_args(argv)
    if args.self_test_update:
        from .updater import install_from_dmg

        dmg, app = map(Path, args.self_test_update)
        install_from_dmg(dmg, app)
        print(f"self-test update ok: installed {dmg.name} into {app}")
        return
    if args.self_test:
        self_test(args.self_test_model, args.self_test_audio, args.self_test_meeting)
        return
    first_run = not CONFIG_PATH.exists()
    config = load_config()
    if args.config_path:
        print(CONFIG_PATH)
        return
    if args.command:
        run_command(args, config)
        return
    if sys.platform != "darwin":
        sys.exit("speech-to-text is a macOS app.")
    validate_hotkey(config.hotkey)

    setup_logging()
    from .app import SpeechToTextApp

    SpeechToTextApp(config, str(LOG_PATH), first_run=first_run).run()


def self_test_meeting(transcriber, wavs: list[Path]) -> None:
    """Run the Notetaker pipeline on two recordings standing in for the microphone and the computer."""
    import tempfile
    import time

    from .meeting_audio import PcmWriter, SystemAudioCapture, _stream_output_class
    from .meeting_notes import process_meeting
    from .meetings import MeetingStore
    from .pipeline import load_wav

    folder = Path(tempfile.mkdtemp(prefix="stt-meeting-test-"))
    audio_dir = folder / "audio"
    audio_dir.mkdir()
    for name, wav in zip(("me.pcm", "others.pcm"), wavs):
        (load_wav(wav) * 32767).astype("<i2").tofile(audio_dir / name)
    store = MeetingStore(folder / "history.db")
    meeting = process_meeting(store.start(audio_dir, True), store, transcriber, None)
    print(f"meeting [{meeting.language}]:\n{meeting.transcript_text()}")
    if not meeting.segments or {s.speaker for s in meeting.segments} != {"me", "others"} or audio_dir.exists():
        sys.exit("self-test: the meeting pipeline didn't produce both tracks")

    # The ScreenCaptureKit glue has to load; actually recording needs a permission CI doesn't have.
    _stream_output_class()
    capture = SystemAudioCapture(PcmWriter(folder / "system.pcm", time.monotonic()))
    try:
        capture.start()
        print("system audio capture: started")
        capture.stop()
    except Exception as exc:
        print(f"system audio capture: {type(exc).__name__}: {exc}")
    capture.writer.close()


def self_test(model: str | None = None, audio_files: list[str] | None = None, meeting: list[str] | None = None) -> None:
    """Check that the packaged app contains everything it needs. Run by the build (no microphone needed)."""
    import importlib

    import mlx.core as mx
    import numpy as np
    from mlx_whisper.audio import log_mel_spectrogram
    from mlx_whisper.tokenizer import get_tokenizer

    from .config import TranscriptionConfig
    from .transcriber import Transcriber

    for module in ("AppKit", "ApplicationServices", "AVFoundation", "Quartz", "ServiceManagement", "rumps",
                   "sounddevice", "mlx_whisper", "ScreenCaptureKit", "CoreMedia", "WebKit"):
        importlib.import_module(module)

    def check_bundle() -> str:
        mel = log_mel_spectrogram(np.zeros(16_000, dtype=np.float32))  # needs the bundled mel filters
        tokens = get_tokenizer(True, num_languages=100, language="pt").encode("olá mundo")  # bundled vocabulary
        return f"mlx {mx.__version__} (metal: {mx.metal.is_available()}), mel {tuple(mel.shape)}, {len(tokens)} tokens"

    # MLX ties arrays (including mlx_whisper's cached mel filters) to the thread that made them, so the
    # check runs on the transcriber's model thread, exactly like everything MLX does in the app.
    transcriber = Transcriber(TranscriptionConfig(model=model or "tiny", languages=["en", "pt"]))
    print(transcriber._on_model_thread(check_bundle))

    if model:
        from .pipeline import load_wav
        from .prompts import whisper_prompt

        transcriber.load()
        for path in audio_files or []:
            audio = load_wav(Path(path))
            language = transcriber.detect_language(audio)
            text = transcriber.transcribe(audio, language, whisper_prompt(language))
            print(f"{Path(path).name}: [{language}] {text}")
            if not text:
                sys.exit(f"self-test: no text transcribed from {path}")
        if meeting:
            self_test_meeting(transcriber, [Path(p) for p in meeting])
    print("self-test ok")


_STATUS_LABELS = {
    "paste_failed": " (paste failed)",
    "filtered": " (filtered as noise, not pasted)",
    "failed": " (transcription failed: run `stt retry`)",
    "recovered": " (recovered by retry)",
}


def run_command(args: argparse.Namespace, config) -> None:
    from .history import HistoryStore
    from .learning import build_profile

    store = HistoryStore()

    if args.command == "history":
        items = store.recent(args.limit, search=args.search, language=args.lang, status=args.status)
        if not items:
            print("No dictations found.")
        for item in reversed(items):  # oldest first, so the newest ends up next to your prompt
            flags = _STATUS_LABELS.get(item.status, "")
            flags += " (corrected)" if item.corrected_text else ""
            where = f" → {item.app_name}" if item.app_name else ""
            print(f"#{item.id}  {item.created_at}  [{item.language or '?'}]{where}{flags}")
            print(f"    {item.best_text}")
            if args.raw and item.raw_text != item.best_text:
                print(f"    heard: {item.raw_text}")
        return

    if args.command == "copy":
        item = store.get(args.id) if args.id else next(iter(store.recent(1, include_unusable=False)), None)
        if not item:
            sys.exit("No such dictation.")
        _copy_to_clipboard(item.best_text, f"#{item.id}")
        return

    if args.command == "retry":
        ids = [args.id] if args.id else [item.id for item in store.recent(1000, status="failed")]
        if not ids:
            print("No failed dictations to retry.")
            return
        from .cleanup import LLMCleaner
        from .pipeline import Pipeline
        from .transcriber import Transcriber

        print("Loading the Whisper model…")
        transcriber = Transcriber(config.transcription)
        transcriber.load()
        pipeline = Pipeline(config, transcriber, LLMCleaner(config.cleanup), store)
        for history_id in ids:
            try:
                text = pipeline.retry(history_id)
            except Exception as exc:
                print(f"#{history_id}: still failing: {exc}")
                continue
            _copy_to_clipboard(text, f"#{history_id}")
        return

    if args.command == "learn":
        # Taught explicitly, so it counts as seen twice: applied as a replacement right away.
        store.add_correction(args.wrong, args.right, weight=2)
        print(f'Learned: "{args.wrong}" → "{args.right}"')
        return

    if args.command == "unlearn":
        removed = store.remove_correction(args.wrong)
        print(f'Forgot {removed} fix(es) for "{args.wrong}".' if removed else f'Nothing learned for "{args.wrong}".')
        return

    if args.command == "profile":
        stats = store.stats()
        profile = build_profile(store)
        print(f"Dictations: {stats['dictations']}  ·  ~{stats['words']} words  ·  {stats['seconds'] / 60:.0f} min of speech")
        print(
            f"Failed pastes: {stats['failed_pastes']}  ·  failed transcriptions: {stats['failed_transcriptions']}"
            f"  ·  corrected by you: {stats['corrected']}"
        )
        if profile.language_counts:
            total = sum(profile.language_counts.values())
            print("Languages: " + ", ".join(f"{lang} {n * 100 // total}%" for lang, n in
                                            sorted(profile.language_counts.items(), key=lambda kv: -kv[1])))
        print("\nLearned vocabulary (sent to Whisper and the cleanup model):")
        print("  " + (", ".join(profile.vocabulary) or "nothing yet: it picks up names/terms you repeat"))
        print("\nLearned fixes (auto-applied once seen twice; always hinted to the cleanup model):")
        for wrong, right, count in store.corrections():
            status = "auto" if count >= 2 else "hint"
            print(f'  "{wrong}" → "{right}"  ×{count} [{status}]')
        if not store.corrections():
            print("  none yet: use 'Fix last transcription…' in the menu, or stt learn WRONG RIGHT")
        return


def _copy_to_clipboard(text: str, label: str) -> None:
    if shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        print(f"Copied {label}: {text}")
    else:
        print(text)


if __name__ == "__main__":
    main()
