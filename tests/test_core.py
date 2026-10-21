import sys
import types

import numpy as np
import pytest

from speech_to_text.config import DEFAULT_CONFIG_TOML, load_config, parse_config
from speech_to_text.hotkey import TapDetector, validate_hotkey
from speech_to_text.prompts import CLEANUP_EXAMPLES, cleanup_messages, whisper_prompt
from speech_to_text.recorder import is_silent, resample
from speech_to_text.config import TranscriptionConfig
from speech_to_text.transcriber import SAMPLE_RATE, Transcriber, resolve_backend

RIGHT_OPTION, RIGHT_OPTION_FLAG, LEFT_SHIFT = 61, 0x40, 56


def test_default_config():
    config = parse_config()
    assert config.hotkey == "right_option"
    assert config.transcription.model == "large-v3-turbo"
    assert config.cleanup.enabled is True
    assert config.transcription.languages == ["en", "pt"]
    assert config.history.enabled and config.history.learn
    assert config.replacements == {}


def test_user_config_overrides_only_what_it_sets():
    config = parse_config(
        'hotkey = "f18"\nbogus = 1\n[cleanup]\nmodel = "gemma3:4b"\nvocabulary = ["Acme"]\n'
        '[replacements]\n"get hub" = "GitHub"\n'
    )
    assert config.hotkey == "f18"
    assert config.cleanup.model == "gemma3:4b"
    assert config.cleanup.ollama_url == "http://localhost:11434"
    assert config.cleanup.vocabulary == ["Acme"]
    assert config.replacements == {"get hub": "GitHub"}


def test_load_config_writes_commented_default(tmp_path):
    path = tmp_path / "config.toml"
    assert load_config(path).hotkey == "right_option"
    assert path.read_text() == DEFAULT_CONFIG_TOML


def test_modifier_tap_alone_toggles():
    d = TapDetector("right_option")
    assert not d.flags_changed(RIGHT_OPTION, RIGHT_OPTION_FLAG)  # press
    assert d.flags_changed(RIGHT_OPTION, 0)  # release -> fire


def test_modifier_used_in_shortcut_does_not_toggle():
    d = TapDetector("right_option")
    d.flags_changed(RIGHT_OPTION, RIGHT_OPTION_FLAG)
    d.key_down(0, autorepeat=False)  # Option+A
    assert not d.flags_changed(RIGHT_OPTION, 0)

    d.flags_changed(RIGHT_OPTION, RIGHT_OPTION_FLAG)
    d.flags_changed(LEFT_SHIFT, RIGHT_OPTION_FLAG | 0x02)  # Option+Shift
    assert not d.flags_changed(RIGHT_OPTION, 0)


def test_left_option_does_not_trigger_right_option():
    d = TapDetector("right_option")
    assert not d.flags_changed(58, 0x20)
    assert not d.flags_changed(58, 0)


def test_function_key_hotkey():
    d = TapDetector("f18")
    assert d.key_down(79, autorepeat=False)
    assert not d.key_down(79, autorepeat=True)
    assert not d.key_down(80, autorepeat=False)


def test_unknown_hotkey():
    with pytest.raises(ValueError):
        validate_hotkey("caps_lock")


def test_whisper_prompt_puts_user_vocabulary_first():
    prompt = whisper_prompt("en", ["Zyxel", "API"])
    assert prompt.index("Zyxel") < prompt.index("JSON")
    assert prompt.count("API") == 1


@pytest.mark.parametrize("vocabulary", [None, [f"fetchUserById{i}" for i in range(60)]])
def test_whisper_prompt_glossary_stays_far_below_whisper_prompt_window(vocabulary):
    # Whisper keeps the last 223 prompt tokens. large-v3-turbo looped ("the most important... the most
    # important...") when the glossary came near that, on dictations that opened with silence: measured
    # 83 glossary tokens looped 0 of 24 times, 197 tokens 3 of 24, 232 tokens 17 of 24. Text is ~2.5 chars/token.
    assert len(whisper_prompt("en", vocabulary)) <= 300
    assert len(whisper_prompt("pt", vocabulary)) <= 300


def test_whisper_prompt_language_and_recent_style():
    recent = " ".join(f"palavra{i}" for i in range(100))
    prompt = whisper_prompt("pt", ["Supabase"], recent)
    assert prompt.startswith("Um engenheiro de software")
    assert prompt.endswith("palavra99")
    assert "palavra59" not in prompt  # only the last 40 words
    assert len(prompt.split()) < 200


def test_cleanup_messages_include_learned_profile():
    messages = cleanup_messages(
        "hello",
        language="pt",
        vocabulary=["Supabase"],
        corrections=[("get hub", "GitHub")],
        examples=[("raw words", "Fixed words.")],
    )
    assert len(messages) == 2 + 2 * (len(CLEANUP_EXAMPLES) + 1)
    assert [m["role"] for m in messages[1:3]] == ["user", "assistant"]
    assert "Supabase" in messages[0]["content"] and '"get hub" -> "GitHub"' in messages[0]["content"]
    assert messages[-2]["content"] == "Fixed words."
    assert 'language="Portuguese"' in messages[-1]["content"]


def test_resample_and_silence():
    tone = np.sin(np.linspace(0, 2000, 48_000)).astype(np.float32) * 0.1
    out = resample(tone, 48_000, SAMPLE_RATE)
    assert out.dtype == np.float32 and len(out) == SAMPLE_RATE
    assert not is_silent(out)
    assert is_silent(np.zeros(SAMPLE_RATE, dtype=np.float32))
    assert is_silent(out[:1000])


class _FakeTranscriber(Transcriber):
    def __init__(self, languages, probabilities, model="large-v3-turbo"):
        super().__init__(TranscriptionConfig(backend="faster-whisper", model=model, languages=languages))
        self._probabilities = probabilities

    def _language_probabilities(self, audio):
        return self._probabilities


def test_language_detection_is_restricted_to_configured_languages():
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    # Whisper thinks it's Galician/Spanish, but among en/pt it's clearly Portuguese.
    probs = {"gl": 0.5, "es": 0.3, "pt": 0.15, "en": 0.05}
    assert _FakeTranscriber(["en", "pt"], probs).detect_language(audio) == "pt"
    assert _FakeTranscriber(["en"], probs).detect_language(audio) == "en"
    assert _FakeTranscriber([], probs).detect_language(audio) == "gl"
    assert _FakeTranscriber(["en", "pt"], probs, model="small.en").detect_language(audio) == "en"


def test_backend_resolution():
    assert resolve_backend("faster-whisper") == "faster-whisper"
    assert resolve_backend("auto") in {"mlx", "faster-whisper"}


def test_set_top_level_value_keeps_the_rest_of_the_file(tmp_path):
    from speech_to_text.config import set_top_level_value

    path = tmp_path / "config.toml"
    path.write_text(DEFAULT_CONFIG_TOML)
    set_top_level_value("hotkey", "fn", path)
    config = load_config(path)
    assert config.hotkey == "fn"
    assert config.transcription.languages == ["en", "pt"]
    assert "Press Esc while recording" in path.read_text()

    path.write_text('[cleanup]\nmodel = "gemma3:4b"\n')
    set_top_level_value("hotkey", "f18", path)
    config = load_config(path)
    assert (config.hotkey, config.cleanup.model) == ("f18", "gemma3:4b")


def _fake_mlx_whisper(monkeypatch, outcomes):
    """outcomes(prompt, kwargs) -> (text, compression_ratio, avg_logprob); returns the calls it saw."""
    calls = []

    def transcribe(audio, *, initial_prompt=None, **kwargs):
        calls.append((initial_prompt, kwargs.get("temperature")))
        text, ratio, logprob = outcomes(initial_prompt, kwargs)
        return {"text": text, "segments": [{"compression_ratio": ratio, "avg_logprob": logprob}]}

    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=transcribe))
    return calls


def _mlx_transcriber():
    return Transcriber(TranscriptionConfig(backend="mlx", model="large-v3-turbo", languages=["en"]))


def test_a_looping_prompted_pass_is_redone_without_the_prompt(monkeypatch):
    # Noise with the glossary prompt came back as "Glossary, JSON, SQL, SQL, ..." (compression ratio 17.5)
    # after 5-28s of temperature retries; without the prompt the same audio was clean.
    calls = _fake_mlx_whisper(
        monkeypatch,
        lambda prompt, kwargs: ("Glossary, JSON, SQL, SQL, SQL", 17.5, -0.17) if prompt else ("I need a hand.", 1.0, -0.2),
    )
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    assert _mlx_transcriber()._run(audio, "en", "A glossary prompt") == "I need a hand."
    assert calls == [("A glossary prompt", 0.0), (None, None)]  # first pass can't spend retries; the redo can


def test_a_confident_prompted_pass_is_kept(monkeypatch):
    calls = _fake_mlx_whisper(monkeypatch, lambda prompt, kwargs: ("Open the Supabase dashboard.", 1.2, -0.25))
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    assert _mlx_transcriber()._run(audio, "en", "A glossary prompt") == "Open the Supabase dashboard."
    assert len(calls) == 1
