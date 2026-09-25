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


class _FakeMenuItem:
    def __init__(self):
        self.title, self.callback = "", None

    def set_callback(self, callback):
        self.callback = callback


def _permission_app(app_module, monkeypatch, missing, restart_pending):
    app = app_module.SpeechToTextApp.__new__(app_module.SpeechToTextApp)
    app.permission_item = _FakeMenuItem()
    app.recorder = types.SimpleNamespace(is_recording=False)
    app.jobs = types.SimpleNamespace(unfinished_tasks=0)
    app.meetings = types.SimpleNamespace(busy=False)
    app._restart_when_listening_allowed = restart_pending
    app.restarts = 0
    app.reload = lambda _item: setattr(app, "restarts", app.restarts + 1)
    monkeypatch.setattr(app_module.permissions, "missing_permissions", lambda: list(missing))
    return app


def test_missing_permission_is_shown_with_a_fix_button(app_module, monkeypatch):
    opened = []
    monkeypatch.setattr(app_module.permissions, "open_settings", opened.append)
    app = _permission_app(app_module, monkeypatch, ["Input Monitoring"], restart_pending=True)
    app._check_permissions()
    assert app.permission_item.title.startswith("⚠️ Allow Input Monitoring")
    app.permission_item.callback(None)
    assert opened == ["Input Monitoring"] and app.restarts == 0


def test_restarts_once_input_monitoring_is_granted(app_module, monkeypatch):
    app = _permission_app(app_module, monkeypatch, [], restart_pending=True)
    app._check_permissions()
    assert app.restarts == 1 and app.permission_item.title == "Permissions: all set ✓"


def test_no_restart_loop_when_nothing_was_pending(app_module, monkeypatch):
    app = _permission_app(app_module, monkeypatch, [], restart_pending=False)
    app._check_permissions()
    assert app.restarts == 0


def test_late_dictation_is_saved_but_not_pasted(app_module, store, monkeypatch):
    app = app_module.SpeechToTextApp.__new__(app_module.SpeechToTextApp)
    app.config = parse_config()
    app.history = store
    app.pipeline = make_pipeline(store, FakeTranscriber("Ship it.", "en"))
    app.last, app.error, app._menu_stale = None, None, False
    pastes = []
    monkeypatch.setattr(app_module, "paste_text", lambda text: pastes.append(text) or True)

    stopped_long_ago = app_module.time.monotonic() - app_module.LATE_PASTE_SECONDS - 5
    app._process(app_module.Job(speech(), "Slack", stopped_at=stopped_long_ago))

    assert pastes == []
    assert store.recent()[0].final_text == "Ship it."
    assert "Recent" in app.error and app.last.text == "Ship it."


def test_recording_is_refused_until_the_model_is_ready(app_module):
    app = app_module.SpeechToTextApp.__new__(app_module.SpeechToTextApp)
    app.config = parse_config()
    app.config.sounds = False
    app.model_ready = False
    started = []
    app.recorder = types.SimpleNamespace(is_recording=False, start=lambda: started.append(True))
    app.toggle_recording()
    assert started == []


def test_model_download_reports_progress(tmp_path, monkeypatch):
    import huggingface_hub
    import huggingface_hub.constants

    from speech_to_text.config import TranscriptionConfig
    from speech_to_text.transcriber import Transcriber

    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path))
    blobs = tmp_path / "models--mlx-community--whisper-large-v3-turbo" / "blobs"

    class FakeApi:
        def model_info(self, repo, files_metadata):
            return types.SimpleNamespace(siblings=[types.SimpleNamespace(size=3000), types.SimpleNamespace(size=None)])

    def fake_snapshot_download(repo):
        blobs.mkdir(parents=True)
        (blobs / "weights.incomplete").write_bytes(b"x" * 1500)
        __import__("time").sleep(1.3)
        (blobs / "weights.incomplete").write_bytes(b"x" * 3000)

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    reports = []
    Transcriber(TranscriptionConfig(backend="mlx")).download(lambda done, total: reports.append((done, total)))
    assert (1500, 3000) in reports
