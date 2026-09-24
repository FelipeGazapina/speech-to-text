"""transcribe -> clean -> save -> paste, with the model and macOS pieces faked."""

import sys
import types

import numpy as np
import pytest

from speech_to_text.config import parse_config
from speech_to_text.history import HistoryStore
from speech_to_text.pipeline import DictationFailed, Pipeline, load_wav, save_wav


class FakeTranscriber:
    def __init__(self, text, language="pt"):
        self.text, self.language, self.prompts = text, language, []

    def detect_language(self, audio):
        return self.language

    def transcribe(self, audio, language, prompt):
        self.prompts.append(prompt)
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


class FakeCleaner:
    def __init__(self, result):
        self.result, self.calls = result, []

    def clean(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return self.result


def speech():
    return (np.sin(np.linspace(0, 4000, 32_000)) * 0.1).astype(np.float32)


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "h.db")


def make_pipeline(store, transcriber, cleaner=None):
    config = parse_config('[replacements]\n"cube control" = "kubectl"\n')
    return Pipeline(config, transcriber, cleaner or FakeCleaner(None), store)


def test_every_dictation_is_cleaned_and_saved(store):
    transcriber = FakeTranscriber("Hmm, roda o cube control apply no cluster")
    cleaner = FakeCleaner("Roda o cube control apply no cluster.")
    outcome = make_pipeline(store, transcriber, cleaner).process(speech(), "Terminal")

    assert outcome.text == "Roda o kubectl apply no cluster."  # replacement re-applied after the LLM
    assert cleaner.calls[0][0] == "Roda o kubectl apply no cluster"
    assert cleaner.calls[0][1]["language"] == "pt"
    assert transcriber.prompts[0].startswith("Um engenheiro de software")
    [saved] = store.recent()
    assert saved.id == outcome.history_id
    assert (saved.language, saved.app_name, saved.status) == ("pt", "Terminal", "saved")
    assert saved.raw_text == "Hmm, roda o cube control apply no cluster"
    assert saved.final_text == "Roda o kubectl apply no cluster."


def test_output_that_looks_like_noise_is_still_saved(store):
    outcome = make_pipeline(store, FakeTranscriber("Obrigado.")).process(speech())
    assert outcome.text == "" and outcome.status == "filtered"
    [saved] = store.recent()
    assert (saved.status, saved.final_text) == ("filtered", "Obrigado.")


def test_failed_transcription_keeps_the_audio_and_can_be_retried(store):
    transcriber = FakeTranscriber(RuntimeError("Metal device lost"))
    pipeline = make_pipeline(store, transcriber)
    with pytest.raises(DictationFailed) as failure:
        pipeline.process(speech(), "Slack")

    saved = store.get(failure.value.history_id)
    assert saved.status == "failed" and "Metal device lost" in saved.error
    assert load_wav(__import__("pathlib").Path(saved.audio_path)).shape == (32_000,)

    transcriber.text = "Recovered text."
    assert pipeline.retry(saved.id) == "Recovered text."
    recovered = store.get(saved.id)
    assert (recovered.status, recovered.final_text, recovered.audio_path) == ("recovered", "Recovered text.", None)
    assert not list(store.audio_dir.iterdir())  # audio deleted once recovered
    with pytest.raises(ValueError):
        pipeline.retry(saved.id)


def test_learned_profile_feeds_the_next_dictation(store):
    transcriber = FakeTranscriber("Open a PR on get hub.", "en")
    store.add_correction("get hub", "GitHub", weight=2)
    outcome = make_pipeline(store, transcriber).process(speech())
    assert outcome.text == "Open a PR on GitHub."
    assert "GitHub" in transcriber.prompts[0]


def test_silence_is_not_saved(store):
    outcome = make_pipeline(store, FakeTranscriber("Thank you.")).process(np.zeros(16_000, dtype=np.float32))
    assert outcome.status == "silent" and store.recent() == []


def test_wav_round_trip(tmp_path):
    audio = speech()
    save_wav(tmp_path / "a.wav", audio)
    assert np.allclose(load_wav(tmp_path / "a.wav"), audio, atol=1e-4)


# --- the app's paste step, with rumps stubbed out ---


@pytest.fixture
def app_module(monkeypatch):
    fake_rumps = types.ModuleType("rumps")
    fake_rumps.App = object
    monkeypatch.setitem(sys.modules, "rumps", fake_rumps)
    sys.modules.pop("speech_to_text.app", None)
    import speech_to_text.app as app_module

    yield app_module
    sys.modules.pop("speech_to_text.app", None)


@pytest.mark.parametrize("pasted, status", [(True, "pasted"), (False, "paste_failed")])
def test_app_records_paste_result(app_module, store, monkeypatch, pasted, status):
    app = app_module.SpeechToTextApp.__new__(app_module.SpeechToTextApp)
    app.config = parse_config()
    app.history = store
    app.pipeline = make_pipeline(store, FakeTranscriber("Ship it.", "en"))
    app.last, app.error, app._menu_stale = None, None, False
    pastes = []
    monkeypatch.setattr(app_module, "paste_text", lambda text: pastes.append(text) or pasted)

    app._process(app_module.Job(speech(), "Slack"))

    assert pastes == ["Ship it. "]
    assert store.recent()[0].status == status
    assert app.last.text == "Ship it." and app._menu_stale
    assert (app.error is None) is pasted
