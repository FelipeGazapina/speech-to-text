"""Entry point of the packaged Speech to Text.app."""

import multiprocessing

# Libraries we use start helper processes (multiprocessing's resource tracker). In a packaged app
# those re-launch this same executable; freeze_support() runs the helper instead of a second app.
multiprocessing.freeze_support()

from speech_to_text.__main__ import main  # noqa: E402

main()
