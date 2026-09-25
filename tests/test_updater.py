from speech_to_text import updater

RELEASE = {
    "tag_name": "v0.2.0",
    "html_url": "https://github.com/FelipeGazapina/speech-to-text/releases/tag/v0.2.0",
    "assets": [
        {"name": "notes.txt", "browser_download_url": "https://example/notes.txt", "size": 1},
        {"name": "SpeechToText.dmg", "browser_download_url": "https://example/SpeechToText.dmg", "size": 150},
    ],
}


def test_parse_version():
    assert updater.parse_version("v0.1.10") == (0, 1, 10)
    assert updater.parse_version("0.1.10") > updater.parse_version("0.1.9")
    assert updater.parse_version("nonsense") == ()


def test_pick_update_offers_only_newer_versions():
    update = updater.pick_update(RELEASE, "0.1.2")
    assert update and update.version == "0.2.0" and update.url.endswith("SpeechToText.dmg") and update.size == 150
    assert updater.pick_update(RELEASE, "0.2.0") is None
    assert updater.pick_update(RELEASE, "0.10.0") is None


def test_pick_update_ignores_drafts_prereleases_and_missing_dmg():
    assert updater.pick_update({**RELEASE, "prerelease": True}, "0.1.0") is None
    assert updater.pick_update({**RELEASE, "draft": True}, "0.1.0") is None
    assert updater.pick_update({**RELEASE, "assets": RELEASE["assets"][:1]}, "0.1.0") is None


def test_same_signer(monkeypatch, tmp_path):
    requirements = {}
    monkeypatch.setattr(updater, "designated_requirement", lambda app: requirements.get(app))
    current, new = tmp_path / "current.app", tmp_path / "new.app"

    requirements[current] = 'identifier "io.github.x" and certificate leaf = H"abc"'
    requirements[new] = 'identifier "io.github.x" and certificate leaf = H"abc"'
    assert updater.same_signer(current, new)

    requirements[new] = 'identifier "io.github.x" and certificate leaf = H"evil"'
    assert not updater.same_signer(current, new)

    requirements[current] = 'cdhash H"1234"'  # ad-hoc builds (before signing) can't be compared
    assert updater.same_signer(current, new)


def test_running_app_path_outside_a_bundle_is_none():
    assert updater.running_app_path() is None
