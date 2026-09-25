"""In-app updates from GitHub Releases: download the new .dmg, swap the app in place, relaunch.

Because the app downloads the update itself (not a browser), macOS doesn't mark it as "from the
internet", so there's no "Not Opened" dialog. And because every release is signed with the same
certificate, macOS keeps the Microphone / Accessibility / Input Monitoring permissions.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

REPO = "FelipeGazapina/speech-to-text"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
DMG_NAME = "SpeechToText.dmg"
APP_NAME = "Speech to Text.app"


@dataclass
class Update:
    version: str
    url: str
    size: int
    notes_url: str


def parse_version(text: str) -> tuple[int, ...]:
    digits = text.strip().lstrip("vV").split("-")[0]
    try:
        return tuple(int(part) for part in digits.split("."))
    except ValueError:
        return ()


def pick_update(release: dict, current_version: str) -> Update | None:
    """The update offered by a GitHub 'latest release' payload, if it's newer than what's running."""
    version = release.get("tag_name", "")
    if release.get("draft") or release.get("prerelease"):
        return None
    if parse_version(version) <= parse_version(current_version):
        return None
    for asset in release.get("assets", []):
        if asset.get("name") == DMG_NAME:
            return Update(version.lstrip("vV"), asset["browser_download_url"], asset.get("size", 0),
                          release.get("html_url", ""))
    return None


def check_for_update(current_version: str) -> Update | None:
    request = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return pick_update(json.loads(response.read()), current_version)


def running_app_path() -> Path | None:
    """The .app bundle we're running from, when packaged (…/Speech to Text.app/Contents/MacOS/…)."""
    if not getattr(sys, "frozen", False):
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def download(update: Update, folder: Path, on_progress: Callable[[int, int], None] | None = None) -> Path:
    target = folder / DMG_NAME
    with urllib.request.urlopen(update.url, timeout=60) as response, open(target, "wb") as out:
        total = int(response.headers.get("Content-Length") or update.size or 0)
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    return target


def install_from_dmg(dmg: Path, app_path: Path) -> None:
    """Replace app_path with the app inside dmg. Refuses an app signed by a different certificate."""
    work = Path(tempfile.mkdtemp(prefix="stt-update-"))
    mount = work / "mount"
    mount.mkdir()
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(dmg)],
                   check=True, capture_output=True)
    try:
        new_app = work / APP_NAME
        subprocess.run(["ditto", str(mount / APP_NAME), str(new_app)], check=True, capture_output=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)

    verify_signature(new_app)
    if not same_signer(app_path, new_app):
        raise RuntimeError("The downloaded update isn't signed by the same certificate as this app; not installing it.")

    # Copy next to the current app first (same disk), then swap with two quick renames.
    staged = app_path.with_name(app_path.name + ".updating")
    old = app_path.with_name(f".{app_path.stem}.old-{int(time.time())}.app")
    shutil.rmtree(staged, ignore_errors=True)
    subprocess.run(["ditto", str(new_app), str(staged)], check=True, capture_output=True)
    os.rename(app_path, old)
    try:
        os.rename(staged, app_path)
    except OSError:
        os.rename(old, app_path)  # put the working version back
        raise
    shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    log.info("Installed update into %s", app_path)


def relaunch(app_path: Path) -> None:
    """Open the (new) app once this process has quit."""
    subprocess.Popen(["/bin/sh", "-c", f'sleep 1; open "{app_path}"'], start_new_session=True)


def verify_signature(app: Path) -> None:
    result = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"The downloaded update's signature is broken: {result.stderr.strip()}")


def designated_requirement(app: Path) -> str | None:
    result = subprocess.run(["codesign", "-d", "-r-", str(app)], capture_output=True, text=True)
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("designated =>"):
            return line.removeprefix("designated =>").strip()
    return None


def same_signer(current: Path, new: Path) -> bool:
    """True if new is signed like current. An ad-hoc current app (older builds) has nothing to compare."""
    current_requirement = designated_requirement(current)
    if not current_requirement or current_requirement.startswith("cdhash"):
        return True
    return designated_requirement(new) == current_requirement
