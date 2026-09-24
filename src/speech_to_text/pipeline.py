"""Audio in, text out: transcribe, clean up, and record every dictation in the history.

Kept free of any UI so the menu bar app and the `stt retry` command share it.
Every recording that contains speech ends up in the history, whatever happens:
- normal text           -> status "saved" (the app then marks it "pasted" / "paste_failed")
- looks like noise      -> status "filtered" (saved, not pasted)
- transcription crashed -> status "failed", with the audio kept on disk for a retry
"""

from __future__ import annotations

import logging
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .cleanup import LLMCleaner, apply_replacements, basic_cleanup
from .config import Config
from .history import HistoryStore
from .learning import Profile, build_profile
from .prompts import whisper_prompt
from .recorder import is_silent
from .transcriber import SAMPLE_RATE, Transcriber

log = logging.getLogger(__name__)


@dataclass
class Transcript:
    raw: str
    text: str  # what to paste; "" when the output was filtered as noise
    language: str | None
    transcribe_seconds: float
    cleanup_seconds: float | None
    cleanup_used: bool


@dataclass
class Outcome:
    history_id: int | None
    text: str  # what to paste; "" means nothing to paste
    status: str  # "saved", "filtered", or "silent" (nothing recorded, nothing saved)


class DictationFailed(Exception):
    def __init__(self, history_id: int | None, cause: Exception):
        super().__init__(str(cause))
        self.history_id = history_id


class Pipeline:
    def __init__(
        self,
        config: Config,
        transcriber: Transcriber,
        cleaner: LLMCleaner,
        history: HistoryStore | None,
        cleanup_enabled: Callable[[], bool] = lambda: True,
    ):
        self.config = config
        self.transcriber = transcriber
        self.cleaner = cleaner
        self.history = history
        self.cleanup_enabled = cleanup_enabled
        self.profile = Profile()
        self._profile_stale = True

    def mark_profile_stale(self) -> None:
        self._profile_stale = True

    def current_profile(self) -> Profile:
        if self._profile_stale and self.history and self.config.history.learn:
            try:
                self.profile = build_profile(self.history)
                log.info(
                    "Profile: %d learned terms, %d corrections, %d examples",
                    len(self.profile.vocabulary), len(self.profile.corrections), len(self.profile.examples),
                )
            except Exception:
                log.exception("Could not build the learning profile")
        self._profile_stale = False
        return self.profile

    # --- the three entry points ---

    def process(self, audio: np.ndarray, app_name: str | None = None) -> Outcome:
        """Transcribe a fresh recording and save it. Raises DictationFailed (audio kept) on errors."""
        if is_silent(audio):
            log.info("Recording was empty or silent; nothing to save")
            return Outcome(None, "", "silent")
        try:
            transcript = self.transcribe(audio)
        except Exception as exc:
            log.exception("Transcription failed")
            raise DictationFailed(self._save_failed(audio, app_name, exc), exc) from exc

        if not transcript.raw.strip():
            return Outcome(None, "", "silent")
        status = "saved" if transcript.text else "filtered"
        history_id = self._save(
            raw_text=transcript.raw,
            # Filtered output is kept verbatim, in case it was real speech ("Obrigado.").
            final_text=transcript.text or transcript.raw.strip(),
            language=transcript.language,
            app_name=app_name,
            audio_seconds=len(audio) / SAMPLE_RATE,
            transcribe_seconds=transcript.transcribe_seconds,
            cleanup_seconds=transcript.cleanup_seconds,
            cleanup_used=transcript.cleanup_used,
            status=status,
        )
        return Outcome(history_id, transcript.text, status)

    def retry(self, history_id: int) -> str:
        """Re-transcribe a failed dictation from its saved audio. Returns the text."""
        if not self.history:
            raise RuntimeError("History is disabled")
        item = self.history.get(history_id)
        if not item or item.status != "failed" or not item.audio_path:
            raise ValueError(f"Dictation #{history_id} has no saved audio to retry")
        audio = load_wav(Path(item.audio_path))
        transcript = self.transcribe(audio)
        text = transcript.text or transcript.raw.strip()
        self.history.record_retry(
            history_id,
            raw_text=transcript.raw,
            final_text=text,
            language=transcript.language,
            cleanup_used=transcript.cleanup_used,
        )
        Path(item.audio_path).unlink(missing_ok=True)
        self.mark_profile_stale()
        return text

    def transcribe(self, audio: np.ndarray) -> Transcript:
        profile = self.current_profile()
        vocabulary = [*self.config.cleanup.vocabulary, *profile.vocabulary]
        # Your explicit replacements win over learned ones.
        replacements = {**profile.replacements, **self.config.replacements}

        started = time.monotonic()
        language = self.transcriber.detect_language(audio)
        prompt = whisper_prompt(language, vocabulary, profile.recent_text.get(language or "", ""))
        raw = self.transcriber.transcribe(audio, language, prompt)
        transcribe_seconds = time.monotonic() - started
        log.info("Whisper [%s]: %r", language, raw)

        text = basic_cleanup(raw, replacements, language)
        cleaned, cleanup_seconds = None, None
        if text and self.cleanup_enabled():
            started = time.monotonic()
            cleaned = self.cleaner.clean(
                text,
                language=language,
                vocabulary=profile.vocabulary,
                corrections=profile.corrections,
                examples=profile.examples,
            )
            cleanup_seconds = time.monotonic() - started
        final = apply_replacements(cleaned, replacements) if cleaned else text
        return Transcript(raw, final, language, transcribe_seconds, cleanup_seconds, cleaned is not None)

    # --- saving ---

    def _save(self, **fields) -> int | None:
        if not self.history:
            return None
        try:
            history_id = self.history.add(**fields)
        except Exception:
            log.exception("Could not save to history")
            return None
        self.mark_profile_stale()
        return history_id

    def _save_failed(self, audio: np.ndarray, app_name: str | None, error: Exception) -> int | None:
        if not self.history:
            return None
        try:
            path = self.history.audio_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.wav"
            save_wav(path, audio)
            return self.history.add(
                raw_text="",
                final_text="",
                app_name=app_name,
                audio_seconds=len(audio) / SAMPLE_RATE,
                status="failed",
                error=f"{type(error).__name__}: {error}",
                audio_path=str(path),
            )
        except Exception:
            log.exception("Could not save the failed recording")
            return None


def save_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(pcm.tobytes())


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as f:
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
    return (pcm.astype(np.float32) / 32767).astype(np.float32)
