"""Local Whisper transcription. Free, offline, nothing leaves the Mac."""

from __future__ import annotations

import logging
import platform
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Callable

import numpy as np

from .config import TranscriptionConfig

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000


class TranscriptionTimeout(TimeoutError):
    """Whisper didn't finish in time. The model thread was replaced; the next call reloads the model."""


def transcription_deadline(audio_seconds: float) -> float:
    """Generous: large-v3-turbo on Apple Silicon does ~10-30x real time, so this only trips on a real hang."""
    return 60 + 2 * audio_seconds

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
        # Every model call runs on this one thread: MLX isn't safe to use from several threads, and
        # a dictation queued behind a long meeting still gets its turn between meeting chunks.
        self._model_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
        self._reload_needed = False
        self.timeouts = 0  # in a row; reset by any call that finishes

    def _on_model_thread(self, fn, *args, timeout: float | None = None, **kwargs):
        """Run fn on the model thread. If it takes longer than timeout seconds, the stuck thread is
        abandoned for a fresh one (which reloads the model first) and TranscriptionTimeout is raised."""
        executor = self._model_thread
        future = executor.submit(self._call, fn, args, kwargs)
        try:
            result = future.result(timeout=timeout)
        except FutureTimeout:
            self.timeouts += 1
            log.error("%s didn't finish within %.0fs: restarting the model thread", fn.__name__, timeout)
            self._replace_model_thread(executor)
            raise TranscriptionTimeout(f"Transcription didn't finish within {timeout:.0f} seconds") from None
        self.timeouts = 0
        return result

    def _call(self, fn, args, kwargs):
        if self._reload_needed:
            self._reload_needed = False
            self._load()
        return fn(*args, **kwargs)

    def _replace_model_thread(self, stuck: ThreadPoolExecutor) -> None:
        if self._model_thread is not stuck:
            return  # another caller already replaced it
        stuck.shutdown(wait=False, cancel_futures=True)
        self._model_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
        # MLX ties arrays to the thread that made them: the new thread must load its own copy.
        if self.backend == "mlx":
            try:
                import mlx_whisper.audio
                from mlx_whisper.transcribe import ModelHolder

                ModelHolder.model = ModelHolder.model_path = None
                for name in dir(mlx_whisper.audio):
                    cache_clear = getattr(getattr(mlx_whisper.audio, name), "cache_clear", None)
                    if cache_clear:
                        cache_clear()
            except Exception:
                log.exception("Couldn't reset the MLX caches")
        self._reload_needed = True

    def model_repo(self) -> str | None:
        """The Hugging Face repo the MLX backend downloads (None for faster-whisper, which manages its own)."""
        if self.backend != "mlx":
            return None
        return _MLX_REPOS.get(self.config.model, self.config.model)

    def download(self, on_progress: Callable[[int, int], None]) -> None:
        """Make sure the model files are on disk, reporting (bytes downloaded, total bytes) about once a second.

        Downloads resume where they stopped, so quitting halfway through isn't a problem.
        """
        repo = self.model_repo()
        if repo is None or Path(repo).exists():  # a local folder: nothing to download
            return
        from huggingface_hub import HfApi, snapshot_download
        from huggingface_hub.constants import HF_HUB_CACHE

        try:
            total = sum(f.size or 0 for f in HfApi().model_info(repo, files_metadata=True).siblings or [])
        except Exception:
            log.warning("Couldn't get the model size; downloading without a percentage", exc_info=True)
            total = 0
        blobs = Path(HF_HUB_CACHE) / f"models--{repo.replace('/', '--')}" / "blobs"
        finished = threading.Event()

        def report() -> None:
            while not finished.wait(1):
                on_progress(_folder_size(blobs), total)

        threading.Thread(target=report, daemon=True).start()
        try:
            snapshot_download(repo)
        finally:
            finished.set()

    def load(self) -> None:
        """Load the model, then run a throwaway pass to warm it up."""
        self._on_model_thread(self._load, timeout=600)

    def _load(self) -> None:
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
            probabilities = self._on_model_thread(self._language_probabilities, audio, timeout=60)
        except TranscriptionTimeout:
            raise
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
        text = self._on_model_thread(
            self._run, audio, language=language, prompt=prompt, timeout=transcription_deadline(len(audio) / SAMPLE_RATE)
        )
        log.info("Transcribed %.1fs of audio in %.2fs", len(audio) / SAMPLE_RATE, time.monotonic() - started)
        return text

    def transcribe_segments(
        self, audio: np.ndarray, language: str | None, prompt: str | None = None
    ) -> list[tuple[float, float, str]]:
        """Like transcribe(), but keeps Whisper's timing: [(start seconds, end seconds, text), ...]."""
        return self._on_model_thread(
            self._run_segments, audio, language, prompt, timeout=transcription_deadline(len(audio) / SAMPLE_RATE)
        )

    def _run_segments(self, audio: np.ndarray, language: str | None, prompt: str | None):
        if self.backend == "mlx":
            import mlx_whisper

            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=_MLX_REPOS.get(self.config.model, self.config.model),
                language=language,
                initial_prompt=prompt,
                condition_on_previous_text=False,
            )
            segments = [(s["start"], s["end"], s["text"]) for s in result.get("segments", [])]
        else:
            if self._model is None:
                raise RuntimeError("Transcriber.load() must be called first")
            found, _info = self._model.transcribe(
                audio, language=language, initial_prompt=prompt, condition_on_previous_text=False, beam_size=5
            )
            segments = [(s.start, s.end, s.text) for s in found]
        return [(float(start), float(end), text.strip()) for start, end, text in segments if text.strip()]

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


def _folder_size(folder: Path) -> int:
    size = 0
    for path in folder.glob("*") if folder.exists() else []:
        try:
            size += path.stat().st_size
        except OSError:  # a partial file renamed while we looked
            pass
    return size
