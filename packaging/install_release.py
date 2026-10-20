"""Install a published release over the app in /Applications, the way the app updates itself.

For a Mac whose installed app can't update on its own (0.2.0's update check fails on TLS). Needs the
`gh` CLI, logged in. Downloads the release's .dmg, checks it is signed by the same certificate as the
installed app (so macOS keeps the Microphone, Accessibility and Input Monitoring permissions), waits
until no meeting is being recorded or transcribed, asks the app to quit, swaps the bundle with the app's
own install routine and relaunches it.

    packaging/install_release.py            # the latest release, after the current meeting
    packaging/install_release.py v0.3.0     # that release
    packaging/install_release.py --now      # don't wait: the new app resumes the unfinished meetings itself
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from speech_to_text import updater  # noqa: E402

APP = Path("/Applications") / updater.APP_NAME
# Same as speech_to_text.config.DATA_DIR, without importing it (that needs the app's Python 3.12+).
DATA_DIR = Path(os.environ.get("STT_DATA_DIR", Path.home() / "Library" / "Application Support" / "speech-to-text"))
LOG = Path.home() / "Library" / "Logs" / "speech-to-text.log"
log = logging.getLogger("install")


def release_dmg_ready(tag: str | None) -> bool:
    command = ["gh", "release", "view", *([tag] if tag else []), "--json", "assets"]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode == 0 and any(a["name"] == updater.DMG_NAME for a in json.loads(result.stdout)["assets"])


def app_running() -> bool:
    return subprocess.run(["pgrep", "-f", f"{APP.name}/Contents/MacOS/"], capture_output=True).returncode == 0


def meetings_busy() -> bool:
    db_path = DATA_DIR / "history.db"
    if not db_path.exists():
        return False
    with sqlite3.connect(db_path) as db:
        (count,) = db.execute("select count(*) from meetings where status in ('recording', 'processing')").fetchone()
    return count > 0


def check_signature(dmg: Path) -> None:
    """Mount, verify, compare the signer with the installed app, unmount. Raises if anything is off."""
    mount = Path(tempfile.mkdtemp(prefix="stt-check-"))
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(dmg)],
                   check=True, capture_output=True)
    try:
        new_app = mount / updater.APP_NAME
        updater.verify_signature(new_app)
        if APP.exists() and not updater.same_signer(APP, new_app):
            raise RuntimeError("The release is signed by a different certificate than the installed app; "
                               "installing it would make macOS forget the permissions.")
        log.info("Signature OK, same signer as the installed app")
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)


def wait_until_idle() -> None:
    """No meeting recording or transcribing, and the app quiet for a minute (no dictation in flight)."""
    quiet_since = None
    while app_running():
        log_quiet = not LOG.exists() or time.time() - LOG.stat().st_mtime > 60
        if not meetings_busy() and log_quiet:
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since >= 60:
                return
        else:
            quiet_since = None
        time.sleep(30)


def quit_app() -> None:
    subprocess.run(["osascript", "-e", f'tell application "{APP.stem}" to quit'], capture_output=True)
    for _ in range(60):
        if not app_running():
            return
        time.sleep(1)
    raise RuntimeError("The app did not quit within 60 s; not installing over a running app.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tag", nargs="?", help="release tag such as v0.3.0 (default: the latest release)")
    parser.add_argument("--now", action="store_true", help="install without waiting for the current meeting")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    deadline = time.time() + 3 * 3600  # a release being built by CI shows up within minutes
    while not release_dmg_ready(args.tag):
        if time.time() > deadline:
            raise SystemExit(f"No release with {updater.DMG_NAME} after 3 hours")
        log.info("Waiting for the release to carry %s", updater.DMG_NAME)
        time.sleep(60)

    work = Path(tempfile.mkdtemp(prefix="stt-release-"))
    subprocess.run(["gh", "release", "download", *([args.tag] if args.tag else []), "-p", updater.DMG_NAME,
                    "-D", str(work), "--clobber"], check=True)
    dmg = work / updater.DMG_NAME
    log.info("Downloaded %s (%d MB)", dmg, dmg.stat().st_size >> 20)
    check_signature(dmg)

    if args.now:
        log.info("Installing now; the new app resumes any unfinished meeting")
    else:
        log.info("Waiting until no meeting is being recorded or transcribed")
        wait_until_idle()
    if app_running():
        quit_app()
    updater.install_from_dmg(dmg, APP)
    subprocess.run(["open", str(APP)], check=True)
    log.info("Installed and relaunched %s", APP)


if __name__ == "__main__":
    main()
