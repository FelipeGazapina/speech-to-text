"""Local Whisper transcription. Free, offline, nothing leaves the Mac."""

from __future__ import annotations

import logging
import platform
import sys
import time

import numpy as np

from .config import TranscriptionConfig

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

# mlx-community repo names aren't uniform, so map the common short names explicitly.
_MLX_REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "medium.en": "mlx-community/whisper-medium.en-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "small.en": "mlx-community/whisper-small.en-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "base.en": "mlx-community/whisper-base.en-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
    "tiny.en": "mlx-community/whisper-tiny.en-mlx",
}


def resolve_backend(backend: str) -> str:
    if backend != "auto":
        return backend
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "mlx"
    return "faster-whisper"


class Transcriber:
    def __init__(self, config: TranscriptionConfig):
        self.config = config
        self.backend = resolve_backend(config.backend)
        self.languages = [lang.strip().lower() for lang in config.languages if lang.strip()]
        if self.config.model.endswith(".en"):
            self.languages = ["en"]
        self._model = None  # faster-whisper model; mlx caches its own

    def load(self) -> None:
        """Download (first run only) and load the model, then run a throwaway pass to warm it up."""
        started = time.monotonic()
        log.info("Loading Whisper model %r with %s backend...", self.config.model, self.backend)
        if self.backend == "faster-whisper":
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.config.model, device="auto", compute_type="int8")
        warm_up_language = self.languages[0] if self.languages else "en"
        self._run(np.zeros(SAMPLE_RATE, dtype=np.float32), language=warm_up_language, prompt=None)
        log.info("Model ready in %.1fs", time.monotonic() - started)

    def detect_language(self, audio: np.ndarray) -> str | None:
        """Pick the spoken language, but only among the configured ones.

        Whisper's open-ended detection often mislabels short Portuguese clips as Spanish or
        Galician; restricting the choice to the languages you actually speak fixes that.
        """
        if len(self.languages) == 1:
            return self.languages[0]
        try:
            probabilities = self._language_probabilities(audio)
        except Exception:
            log.exception("Language detection failed")
            return self.languages[0] if self.languages else None
        if not self.languages:
            return max(probabilities, key=probabilities.get)
        best = max(self.languages, key=lambda lang: probabilities.get(lang, 0.0))
        log.info("Language: %s (%s)", best, ", ".join(f"{l}={probabilities.get(l, 0.0):.2f}" for l in self.languages))
        return best

    def transcribe(self, audio: np.ndarray, language: str | None, prompt: str | None) -> str:
        started = time.monotonic()
        text = self._run(audio, language=language, prompt=prompt)
        log.info("Transcribed %.1fs of audio in %.2fs", len(audio) / SAMPLE_RATE, time.monotonic() - started)
        return text

    def _language_probabilities(self, audio: np.ndarray) -> dict[str, float]:
        if self.backend == "mlx":
            import mlx.core as mx
            from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
            from mlx_whisper.transcribe import ModelHolder

            model = ModelHolder.get_model(_MLX_REPOS.get(self.config.model, self.config.model), mx.float16)
            mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels, padding=N_SAMPLES)
            segment = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
            _tokens, probabilities = model.detect_language(segment)
            return {lang: float(p) for lang, p in probabilities.items()}

        if self._model is None:
            raise RuntimeError("Transcriber.load() must be called first")
        _language, _probability, all_probabilities = self._model.detect_language(audio)
        return {lang: float(p) for lang, p in all_probabilities}

    def _run(self, audio: np.ndarray, language: str | None, prompt: str | None) -> str:
        if self.backend == "mlx":
            import mlx_whisper

            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=_MLX_REPOS.get(self.config.model, self.config.model),
                language=language,
                initial_prompt=prompt,
                # Each dictation is independent; conditioning on previous windows
                # makes long recordings prone to repetition loops.
                condition_on_previous_text=False,
            )
            return str(result.get("text", "")).strip()

        if self._model is None:
            raise RuntimeError("Transcriber.load() must be called first")
        segments, _info = self._model.transcribe(
            audio,
            language=language,
            initial_prompt=prompt,
            condition_on_previous_text=False,
            vad_filter=True,
            beam_size=5,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
