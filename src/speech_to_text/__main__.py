from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import CONFIG_PATH, load_config
from .hotkey import validate_hotkey

LOG_PATH = Path.home() / "Library" / "Logs" / "speech-to-text.log"


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=2),
        logging.StreamHandler(),
    ]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stt", description="Tap a key, talk, tap again: text appears at your cursor.")
    parser.add_argument("--config-path", action="store_true", help="print the config file location and exit")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    history = commands.add_parser("history", help="list past dictations (newest first)")
    history.add_argument("search", nargs="?", help="only show dictations containing this text")
    history.add_argument("-n", "--limit", type=int, default=20)
    history.add_argument("--lang", help="only this language, e.g. pt or en")
    history.add_argument("--raw", action="store_true", help="also show what Whisper heard before cleanup")

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
    args = build_parser().parse_args(argv)
    config = load_config()
    if args.config_path:
        print(CONFIG_PATH)
        return
    if args.command:
        run_command(args)
        return
    if sys.platform != "darwin":
        sys.exit("speech-to-text is a macOS app.")
    validate_hotkey(config.hotkey)

    setup_logging()
    from .app import SpeechToTextApp

    SpeechToTextApp(config, str(LOG_PATH)).run()


def run_command(args: argparse.Namespace) -> None:
    from .history import HistoryStore
    from .learning import build_profile

    store = HistoryStore()

    if args.command == "history":
        items = store.recent(args.limit, search=args.search, language=args.lang)
        if not items:
            print("No dictations found.")
        for item in reversed(items):  # oldest first, so the newest ends up next to your prompt
            flags = " (paste failed)" if item.pasted == 0 else ""
            flags += " (corrected)" if item.corrected_text else ""
            where = f" → {item.app_name}" if item.app_name else ""
            print(f"#{item.id}  {item.created_at}  [{item.language or '?'}]{where}{flags}")
            print(f"    {item.best_text}")
            if args.raw and item.raw_text != item.best_text:
                print(f"    heard: {item.raw_text}")
        return

    if args.command == "copy":
        item = store.get(args.id) if args.id else next(iter(store.recent(1)), None)
        if not item:
            sys.exit("No such dictation.")
        if shutil.which("pbcopy"):
            subprocess.run(["pbcopy"], input=item.best_text.encode(), check=True)
            print(f"Copied #{item.id}: {item.best_text}")
        else:
            print(item.best_text)
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
        print(f"Failed pastes: {stats['failed_pastes']}  ·  corrected by you: {stats['corrected']}")
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


if __name__ == "__main__":
    main()
