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


def test_whisper_prompt_puts_user_vocabulary_first_and_stays_short():
    prompt = whisper_prompt("en", ["Zyxel", "API"])
    assert prompt.index("Zyxel") < prompt.index("JSON")
    assert prompt.count("API") == 1
    assert len(prompt.split()) < 200


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
