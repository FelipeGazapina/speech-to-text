"""Turning a raw Whisper transcript into the text the speaker meant to type.

Two layers:
- basic_cleanup: deterministic, always on (filler words, spacing, user replacements).
- LLMCleaner: optional "reasoning" pass through a local Ollama model. Any failure
  (Ollama not running, timeout, suspicious output) falls back to the basic text.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request

from .config import CleanupConfig
from .prompts import cleanup_messages

log = logging.getLogger(__name__)

def _filler_pattern(words: str) -> re.Pattern:
    return re.compile(rf"(?<!\w)(?:{words})(?!\w)[,.]?\s*", re.IGNORECASE)


# Hesitation sounds only; real filler *words* (like, tipo, né) are left to the cleanup model.
# "um" is only a filler in English: in Portuguese it's the word "a/one" ("um bug").
_FILLERS_ANY_LANGUAGE = _filler_pattern(r"u+h+|a+h+n+|e+r+m+|h+m+|m+h*m+")
_FILLERS_ENGLISH = _filler_pattern(r"u+m+|u+h+|a+h+n*|e+r+m+|h+m+|m+h*m+")
# Whisper's classic output on silence/noise. Dropped only when it's the ENTIRE transcript.
_HALLUCINATIONS = {
    "", "you", "thank you", "thanks", "thank you for watching", "thanks for watching",
    "bye", "subtitles by the amara.org community", ".",
    "obrigado", "obrigada", "tchau", "legendas pela comunidade amara.org",
}


def basic_cleanup(text: str, replacements: dict[str, str] | None = None, language: str | None = "en") -> str:
    starts_sentence = text.strip()[:1].isupper()
    text = (_FILLERS_ENGLISH if language == "en" else _FILLERS_ANY_LANGUAGE).sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"^[,.;:]\s*", "", text)
    if _normalize(text) in _HALLUCINATIONS:
        return ""
    text = apply_replacements(text, replacements)
    # Removing a leading "Um," can leave the sentence starting lowercase.
    if starts_sentence and text[:1].islower() and not _looks_like_code(text.split()[0]):
        text = text[0].upper() + text[1:]
    return text


def apply_replacements(text: str, replacements: dict[str, str] | None) -> str:
    """Case-insensitive, whole-word find/replace; longest phrases first."""
    for spoken, written in sorted((replacements or {}).items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"(?<!\w){re.escape(spoken)}(?!\w)", lambda _: written, text, flags=re.IGNORECASE)
    return text


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s.]", "", text).strip(" .").lower()


def _looks_like_code(word: str) -> bool:
    # camelCase, snake_case, paths, flags: leave their casing alone.
    return bool(re.search(r"[A-Z_./\-]", word[1:])) or word.startswith("-")


class LLMCleaner:
    def __init__(self, config: CleanupConfig):
        self.config = config
        self._reachable: bool | None = None
        self._checked_at = 0.0
        self._warned_unreachable = False

    def is_available(self) -> bool:
        """Is Ollama up? Cached for 30s so a stopped Ollama doesn't slow every dictation."""
        if not self.config.enabled:
            return False
        if self._reachable is not None and time.monotonic() - self._checked_at < 30:
            return self._reachable
        try:
            with urllib.request.urlopen(f"{self.config.ollama_url}/api/tags", timeout=1):
                self._reachable = True
        except (urllib.error.URLError, OSError):
            if not self._warned_unreachable:  # once per run, not every check
                log.warning("Ollama not reachable at %s; pasting raw transcripts", self.config.ollama_url)
                self._warned_unreachable = True
            self._reachable = False
        self._checked_at = time.monotonic()
        return self._reachable

    def warm_up(self) -> None:
        """Load the model into memory now so the first dictation isn't slow."""
        if self.is_available():
            self._post({"model": self.config.model, "messages": [], "keep_alive": "30m"})

    def setup_state(self) -> str:
        """One of: disabled, no_ollama, missing_model, ready. Refreshes the cached reachability."""
        if not self.config.enabled:
            return "disabled"
        self._reachable = None
        if not self.is_available():
            return "no_ollama"
        return "ready" if self.has_model() else "missing_model"

    def has_model(self) -> bool:
        with urllib.request.urlopen(f"{self.config.ollama_url}/api/tags", timeout=5) as response:
            names = {m.get("name", "") for m in json.loads(response.read()).get("models", [])}
        wanted = self.config.model
        return wanted in names or f"{wanted}:latest" in names

    def download_model(self) -> None:
        """Have Ollama download the cleanup model (a few GB, one time). Blocks until done."""
        log.info("Downloading cleanup model %s through Ollama...", self.config.model)
        request = urllib.request.Request(
            f"{self.config.ollama_url}/api/pull",
            data=json.dumps({"model": self.config.model, "stream": False}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3 * 3600) as response:
            result = json.loads(response.read())
        if result.get("status") != "success":
            raise RuntimeError(f"Ollama could not download {self.config.model}: {result}")
        log.info("Cleanup model %s is ready", self.config.model)

    def clean(
        self,
        text: str,
        *,
        language: str | None = None,
        vocabulary: list[str] | None = None,
        corrections: list[tuple[str, str]] | None = None,
        examples: list[tuple[str, str]] | None = None,
    ) -> str | None:
        """Return the cleaned text, or None to signal 'use the basic text instead'."""
        if not text or not self.is_available():
            return None
        messages = cleanup_messages(
            text,
            language=language,
            vocabulary=[*self.config.vocabulary, *(vocabulary or [])],
            corrections=corrections,
            examples=examples,
        )
        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0},
        }
        started = time.monotonic()
        try:
            response = self._post(payload)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("Cleanup model failed (%s); pasting raw transcript", exc)
            self._reachable = None
            return None
        cleaned = strip_model_output(response.get("message", {}).get("content", ""))
        log.info("Cleanup took %.2fs", time.monotonic() - started)
        if not is_plausible_cleanup(text, cleaned):
            log.warning("Discarding implausible cleanup output: %r", cleaned)
            return None
        return cleaned

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.config.ollama_url}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
            return json.loads(response.read())


def strip_model_output(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?transcript[^>]*>", "", text)
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1].strip()
    return text


def is_plausible_cleanup(raw: str, cleaned: str) -> bool:
    """Guard against the model answering the transcript instead of cleaning it."""
    if not cleaned:
        return False
    if len(cleaned) > 1.5 * len(raw) + 40:
        return False
    if len(raw) > 60 and len(cleaned) < 0.25 * len(raw):
        return False
    return True
