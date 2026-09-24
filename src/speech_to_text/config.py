"""Configuration: a commented TOML file in ~/.config/speech-to-text/config.toml.

The default file below is the single source of truth for defaults. The user's
file is merged on top of it, so a user file only needs the keys it changes.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("STT_CONFIG_DIR", Path.home() / ".config" / "speech-to-text"))
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_CONFIG_TOML = """\
# speech-to-text configuration. Restart the app (menu bar -> Reload config) after editing.

# Key that starts/stops recording. Tap it once to start, tap again to stop.
# A modifier only toggles when tapped ALONE, so Option+letter shortcuts still work.
# Options: right_option, left_option, right_command, right_control, right_shift, fn,
#          f13 ... f19 (great if you remap a key with Karabiner-Elements).
# If you use "fn", set System Settings -> Keyboard -> "Press 🌐 key to" = "Do Nothing".
hotkey = "right_option"

# Press Esc while recording to throw the recording away.
esc_cancels = true

# Play a short sound when recording starts/stops.
sounds = true

# Add a space after the pasted text so consecutive dictations don't run together.
append_space = true

[transcription]
# "auto" = mlx on Apple Silicon, faster-whisper otherwise. Or force "mlx" / "faster-whisper".
backend = "auto"
# Whisper model. "large-v3-turbo" is the best speed/quality trade-off on an M-series Mac.
# Smaller/faster: "small", "small.en", "base.en". Any Hugging Face repo id also works.
model = "large-v3-turbo"
# Spoken language code ("en", "pt", ...). Empty string = auto-detect (less reliable on short clips).
language = "en"

[cleanup]
# The "reasoning" step: a free local LLM (via Ollama) rewrites the raw transcript -
# drops filler words, applies "no wait, I mean..." corrections, formats identifiers
# (camelCase, snake_case, file.ts, CLI flags). Needs Ollama running; if it isn't,
# the raw Whisper text is pasted instead.
enabled = true
ollama_url = "http://localhost:11434"
model = "qwen2.5:3b"
# Seconds to wait for the LLM before giving up and pasting the raw transcript.
timeout = 15

# Extra words/names you say often. They are fed to Whisper (so it spells them right)
# and to the cleanup model. Project names, libraries, teammates, internal tools...
vocabulary = []

# Deterministic find/replace applied to every transcript (case-insensitive, whole words).
# Use it for things Whisper keeps getting wrong.
[replacements]
# "cube control" = "kubectl"
# "get hub" = "GitHub"
"""


@dataclass
class TranscriptionConfig:
    backend: str = "auto"
    model: str = "large-v3-turbo"
    language: str = "en"


@dataclass
class CleanupConfig:
    enabled: bool = True
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen2.5:3b"
    timeout: float = 15
    vocabulary: list[str] = field(default_factory=list)


@dataclass
class Config:
    hotkey: str = "right_option"
    esc_cancels: bool = True
    sounds: bool = True
    append_space: bool = True
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    replacements: dict[str, str] = field(default_factory=dict)


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _known_fields(cls, data: dict) -> dict:
    """Drop keys the dataclass doesn't know, so a typo in the file doesn't crash the app."""
    return {k: v for k, v in data.items() if k in cls.__dataclass_fields__}


def parse_config(user_toml: str = "") -> Config:
    data = _deep_merge(tomllib.loads(DEFAULT_CONFIG_TOML), tomllib.loads(user_toml))
    transcription = TranscriptionConfig(**_known_fields(TranscriptionConfig, data.pop("transcription", {})))
    cleanup = CleanupConfig(**_known_fields(CleanupConfig, data.pop("cleanup", {})))
    replacements = {str(k): str(v) for k, v in data.pop("replacements", {}).items()}
    for nested in ("transcription", "cleanup", "replacements"):
        data.pop(nested, None)
    return Config(
        **_known_fields(Config, data),
        transcription=transcription,
        cleanup=cleanup,
        replacements=replacements,
    )


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load the user's config, writing the commented default file on first run."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TOML)
    return parse_config(path.read_text())
