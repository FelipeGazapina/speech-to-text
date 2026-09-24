from __future__ import annotations

import argparse
import logging
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="stt", description="Tap a key, talk, tap again: text appears at your cursor.")
    parser.add_argument("--config-path", action="store_true", help="print the config file location and exit")
    args = parser.parse_args()

    config = load_config()
    if args.config_path:
        print(CONFIG_PATH)
        return
    if sys.platform != "darwin":
        sys.exit("speech-to-text is a macOS app.")
    validate_hotkey(config.hotkey)

    setup_logging()
    from .app import SpeechToTextApp

    SpeechToTextApp(config, str(LOG_PATH)).run()


if __name__ == "__main__":
    main()
