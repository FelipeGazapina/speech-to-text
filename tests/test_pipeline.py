"""The app's transcribe -> clean -> save -> paste pipeline, with macOS pieces stubbed out."""

import sys
import types

import numpy as np
import pytest

from speech_to_text.config import parse_config
from speech_to_text.history import HistoryStore
from speech_to_text.learning import Profile


@pytest.fixture
def app_module(monkeypatch):
    fake_rumps = types.ModuleType("rumps")
    fake_rumps.App = object
    monkeypatch.setitem(sys.modules, "rumps", fake_rumps)
    sys.modules.pop("speech_to_text.app", None)
    import speech_to_text.app as app_module

    yield app_module
    sys.modules.pop("speech_to_text.app", None)


class FakeTranscriber:
    def __init__(self, text, language="pt"):
        self.text, self.language, self.prompts = text, language, []

    def detect_language(self, audio):
        return self.language

    def transcribe(self, audio, language, prompt):
        self.prompts.append(prompt)
        return self.text


class FakeCleaner:
    def __init__(self, result):
        self.result, self.calls = result, []

    def clean(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return self.result


def make_app(app_module, tmp_path, transcriber, cleaner, pasted=True, monkeypatch=None):
    app = app_module.SpeechToTextApp.__new__(app_module.SpeechToTextApp)
    app.config = parse_config('[replacements]\n"cube control" = "kubectl"\n')
    app.config.sounds = False
    app.history = HistoryStore(tmp_path / "h.db")
    app.profile, app._profile_stale, app._menu_stale = Profile(), True, False
    app.transcriber, app.cleaner = transcriber, cleaner
    app.cleanup_item = types.SimpleNamespace(state=1)
    app.last, app.error = None, None
    pastes = []
    monkeypatch.setattr(app_module, "paste_text", lambda text: pastes.append(text) or pasted)
    return app, pastes


def speech():
    return (np.sin(np.linspace(0, 4000, 32_000)) * 0.1).astype(np.float32)


def test_dictation_is_cleaned_saved_and_pasted(app_module, tmp_path, monkeypatch):
    transcriber = FakeTranscriber("Hmm, roda o cube control apply no cluster")
    cleaner = FakeCleaner("Roda o cube control apply no cluster.")
    app, pastes = make_app(app_module, tmp_path, transcriber, cleaner, monkeypatch=monkeypatch)

    app._process(app_module.Job(speech(), "Terminal"))

    assert pastes == ["Roda o kubectl apply no cluster. "]  # replacement re-applied after the LLM
    assert cleaner.calls[0][0] == "Roda o kubectl apply no cluster"
    assert cleaner.calls[0][1]["language"] == "pt"
    assert transcriber.prompts[0].startswith("Um engenheiro de software")
    [saved] = app.history.recent()
    assert (saved.language, saved.app_name, saved.pasted) == ("pt", "Terminal", 1)
    assert saved.raw_text == "Hmm, roda o cube control apply no cluster"
    assert app.last.text == "Roda o kubectl apply no cluster."


def test_failed_paste_is_recorded_and_text_kept(app_module, tmp_path, monkeypatch):
    app, pastes = make_app(
        app_module, tmp_path, FakeTranscriber("Hello there.", "en"), FakeCleaner(None), pasted=False,
        monkeypatch=monkeypatch,
    )
    app._process(app_module.Job(speech(), None))
    [saved] = app.history.recent()
    assert saved.pasted == 0 and saved.final_text == "Hello there."
    assert "clipboard" in app.error


def test_learned_profile_feeds_the_next_dictation(app_module, tmp_path, monkeypatch):
    transcriber = FakeTranscriber("Open a PR on get hub.", "en")
    cleaner = FakeCleaner(None)
    app, pastes = make_app(app_module, tmp_path, transcriber, cleaner, monkeypatch=monkeypatch)
    app.history.add_correction("get hub", "GitHub", weight=2)

    app._process(app_module.Job(speech(), None))

    assert pastes == ["Open a PR on GitHub. "]
    assert "GitHub" in transcriber.prompts[0]


def test_silence_is_ignored(app_module, tmp_path, monkeypatch):
    app, pastes = make_app(app_module, tmp_path, FakeTranscriber("Thank you."), FakeCleaner(None), monkeypatch=monkeypatch)
    app._process(app_module.Job(np.zeros(16_000, dtype=np.float32), None))
    assert pastes == [] and app.history.recent() == []
