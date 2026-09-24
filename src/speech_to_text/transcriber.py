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
    def __init__(self, config: TranscriptionConfig, prompt: str):
        self.config = config
        self.prompt = prompt
        self.backend = resolve_backend(config.backend)
        self.language = config.language or None
        self._model = None  # faster-whisper model; mlx caches its own

    def load(self) -> None:
        """Download (first run only) and load the model, then run a throwaway pass to warm it up."""
        started = time.monotonic()
        log.info("Loading Whisper model %r with %s backend...", self.config.model, self.backend)
        if self.backend == "faster-whisper":
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.config.model, device="auto", compute_type="int8")
        self._run(np.zeros(SAMPLE_RATE, dtype=np.float32), prompt=None)
        log.info("Model ready in %.1fs", time.monotonic() - started)

    def transcribe(self, audio: np.ndarray) -> str:
        started = time.monotonic()
        text = self._run(audio, prompt=self.prompt)
        log.info("Transcribed %.1fs of audio in %.2fs", len(audio) / SAMPLE_RATE, time.monotonic() - started)
        return text

    def _run(self, audio: np.ndarray, prompt: str | None) -> str:
        if self.backend == "mlx":
            import mlx_whisper

            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=_MLX_REPOS.get(self.config.model, self.config.model),
                language=self.language,
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
            language=self.language,
            initial_prompt=prompt,
            condition_on_previous_text=False,
            vad_filter=True,
            beam_size=5,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
