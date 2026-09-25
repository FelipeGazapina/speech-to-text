"""Turning a recorded meeting into a transcript and a summary.

1. Find where people actually speak in each track (skipping silence also stops Whisper from
   inventing "Thank you." out of nothing).
2. Transcribe those stretches, keeping timestamps, labelled "me" (microphone) or "others"
   (the computer's audio).
3. Merge both tracks by time, dropping the microphone's copy of what came out of the speakers.
4. Summarize with the local Ollama model (in chunks, so long meetings fit).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

import numpy as np

from .cleanup import _HALLUCINATIONS, _normalize
from .config import CleanupConfig
from .meetings import Meeting, MeetingStore, Segment, format_timestamp
from .transcriber import SAMPLE_RATE, Transcriber

log = logging.getLogger(__name__)

TRACKS = {"me": "me.pcm", "others": "others.pcm"}  # raw 16 kHz mono int16, see meeting_audio.py


def load_pcm(path: Path) -> np.ndarray:
    return (np.fromfile(path, dtype="<i2").astype(np.float32) / 32767).astype(np.float32)


# --- 1. where is someone speaking? ---


def speech_regions(
    audio: np.ndarray,
    rate: int = SAMPLE_RATE,
    frame_seconds: float = 0.03,
    min_rms: float = 0.006,
    merge_gap: float = 0.8,
    min_length: float = 0.3,
    padding: float = 0.25,
) -> list[tuple[int, int]]:
    """(start, end) sample ranges that contain speech, found by loudness relative to the noise floor."""
    frame = int(rate * frame_seconds)
    count = len(audio) // frame
    if count == 0:
        return []
    rms = np.sqrt(np.mean(np.square(audio[: count * frame].reshape(count, frame)), axis=1))
    # Above the background noise, but never so high that audio which is all speech (no quiet
    # stretch to measure the noise on) counts as silence: normal speech is well above 0.02.
    threshold = float(np.clip(np.percentile(rms, 20) * 2.5, min_rms, 0.02))
    loud = rms > threshold

    regions: list[list[int]] = []
    for index in np.flatnonzero(loud):
        if regions and index - regions[-1][1] <= merge_gap / frame_seconds:
            regions[-1][1] = index + 1
        else:
            regions.append([index, index + 1])
    pad = int(padding / frame_seconds)
    result = []
    for start, end in regions:
        if (end - start) * frame_seconds < min_length:
            continue
        result.append((max(0, (start - pad) * frame), min(len(audio), (end + pad) * frame)))
    return result


# --- 2. transcribe one track ---


def transcribe_track(
    transcriber: Transcriber,
    audio: np.ndarray,
    speaker: str,
    on_region_done: Callable[[], None] | None = None,
    regions: list[tuple[int, int]] | None = None,
) -> tuple[list[Segment], str | None]:
    regions = speech_regions(audio) if regions is None else regions
    if not regions:
        return [], None
    # Detect the language once per track, on up to 30 s of actual speech.
    sample = np.concatenate([audio[start:end] for start, end in regions])[: 30 * SAMPLE_RATE]
    language = transcriber.detect_language(sample)

    segments: list[Segment] = []
    for start, end in regions:
        offset = start / SAMPLE_RATE
        for seg_start, seg_end, text in transcriber.transcribe_segments(audio[start:end], language):
            if _normalize(text) in _HALLUCINATIONS and seg_end - seg_start < 3:
                continue
            segments.append(Segment(offset + seg_start, offset + seg_end, speaker, text))
        if on_region_done:
            on_region_done()
    return segments, language


# --- 3. merge both tracks ---


def merge_tracks(mine: list[Segment], others: list[Segment], window: float = 2.0) -> list[Segment]:
    """Interleave by time. Without headphones the microphone also hears the other people, so a
    "me" segment that matches an "others" segment said at the same moment is an echo: dropped."""

    def words(text: str) -> str:
        return " ".join(re.findall(r"\w+", text.lower()))

    kept = []
    for segment in mine:
        echo = any(
            other.start - window <= segment.start <= other.end + window
            and SequenceMatcher(None, words(segment.text), words(other.text)).ratio() >= 0.6
            for other in others
        )
        if not echo:
            kept.append(segment)
    return sorted([*kept, *others], key=lambda s: (s.start, s.speaker))


# --- 4. summary ---

HEADINGS = {
    "pt": ("Resumo", "Pontos principais", "Decisões", "Próximos passos"),
    "en": ("Summary", "Key points", "Decisions", "Action items"),
}
LANGUAGE_NAMES = {"pt": "Brazilian Portuguese", "en": "English"}
CHUNK_WORDS = 2500


class Summarizer:
    """Meeting summaries with the same local Ollama model as the dictation cleanup."""

    def __init__(self, config: CleanupConfig, timeout: float = 900):
        self.config = config
        self.timeout = timeout

    def available(self) -> bool:
        if not self.config.enabled:
            return False
        try:
            with urllib.request.urlopen(f"{self.config.ollama_url}/api/tags", timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def summarize(self, segments: list[Segment], language: str | None, labels: dict[str, str]) -> tuple[str, str]:
        """Returns (title, Markdown summary)."""
        lines = [f"[{format_timestamp(s.start)}] {labels.get(s.speaker, s.speaker)}: {s.text}" for s in segments]
        chunks = _chunk_lines(lines, CHUNK_WORDS)
        lang = language if language in HEADINGS else "en"
        if len(chunks) > 1:
            # Long meeting: take notes on each part first, then summarize the notes.
            notes = [self._chat(_notes_prompt(lang), chunk) for chunk in chunks]
            material = "\n\n".join(f"Notes on part {i + 1}:\n{n}" for i, n in enumerate(notes))
        else:
            material = chunks[0] if chunks else ""
        return parse_summary(self._chat(_summary_prompt(lang), material))

    def _chat(self, system: str, content: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.2, "num_ctx": 8192},
        }
        request = urllib.request.Request(
            f"{self.config.ollama_url}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            text = json.loads(response.read()).get("message", {}).get("content", "")
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _chunk_lines(lines: list[str], max_words: int) -> list[str]:
    chunks, current, words = [], [], 0
    for line in lines:
        count = len(line.split())
        if current and words + count > max_words:
            chunks.append("\n".join(current))
            current, words = [], 0
        current.append(line)
        words += count
    if current:
        chunks.append("\n".join(current))
    return chunks


def _notes_prompt(lang: str) -> str:
    return (
        "You take notes on one part of a meeting transcript. List, as short bullet points, everything that "
        "matters: topics discussed, decisions, action items (with who owns them), numbers, dates and names. "
        f"Write in {LANGUAGE_NAMES[lang]}. Output only the bullet points."
    )


def _summary_prompt(lang: str) -> str:
    summary, points, decisions, actions = HEADINGS[lang]
    return (
        "You write meeting notes from a transcript (or from notes on its parts). Lines are marked with who "
        "spoke. Be faithful: never invent facts, owners or dates; if there were no decisions or action "
        f"items, write \"-\" under that heading. Write in {LANGUAGE_NAMES[lang]}. Use exactly this Markdown:\n\n"
        "# <a specific title, at most 8 words>\n\n"
        f"## {summary}\n<3 to 6 sentences: what the meeting was about and where it landed>\n\n"
        f"## {points}\n- <point>\n\n"
        f"## {decisions}\n- <decision>\n\n"
        f"## {actions}\n- [ ] <owner, if known>: <task>\n"
    )


def parse_summary(text: str) -> tuple[str, str]:
    """Split the model's output into (title, body)."""
    text = text.strip()
    match = re.match(r"#\s+(.+?)\s*(?:\n|$)", text)
    if not match:
        return "", text
    return match.group(1).strip().strip("*"), text[match.end():].strip()


# --- the whole job ---


def process_meeting(
    meeting_id: int,
    store: MeetingStore,
    transcriber: Transcriber,
    summarizer: Summarizer | None,
    on_progress: Callable[[str], None] | None = None,
) -> Meeting:
    """Transcribe (and summarize) a recorded meeting, then delete its audio. Safe to re-run after a crash."""
    meeting = store.get(meeting_id)
    if meeting is None:
        raise ValueError(f"No meeting #{meeting_id}")
    report = on_progress or (lambda message: None)

    if meeting.audio_dir:
        store.update(meeting_id, status="processing", error=None)
        folder = Path(meeting.audio_dir)
        audio = {speaker: load_pcm(folder / name) for speaker, name in TRACKS.items() if (folder / name).exists()}
        if meeting.duration_seconds is None and audio:
            store.update(meeting_id, duration_seconds=max(len(a) for a in audio.values()) / SAMPLE_RATE)
        regions = {speaker: speech_regions(track) for speaker, track in audio.items()}
        total = sum(len(r) for r in regions.values()) or 1
        done = 0

        def region_done() -> None:
            nonlocal done
            done += 1
            report(f"Transcribing the meeting… {done * 100 // total}%")

        try:
            results = {
                speaker: transcribe_track(transcriber, audio[speaker], speaker, region_done, regions[speaker])
                for speaker in audio
            }
        except Exception as exc:
            store.update(meeting_id, status="failed", error=f"Transcription failed: {exc}")
            raise
        segments = merge_tracks(results.get("me", ([], None))[0], results.get("others", ([], None))[0])
        # The language most of the meeting was spoken in: whichever track said more decides.
        spoken: dict[str, int] = {}
        for track_segments, track_language in results.values():
            if track_language:
                spoken[track_language] = spoken.get(track_language, 0) + sum(len(s.text) for s in track_segments)
        language = max(spoken, key=spoken.get) if spoken else None
        store.update(meeting_id, segments=segments, language=language, audio_dir=None)
        shutil.rmtree(folder, ignore_errors=True)  # the audio is never kept
        meeting = store.get(meeting_id)

    store.update(meeting_id, status="done")
    if summarizer and meeting.segments:
        summarize_meeting(meeting_id, store, summarizer, report)
    return store.get(meeting_id)


def summarize_meeting(
    meeting_id: int, store: MeetingStore, summarizer: Summarizer, on_progress: Callable[[str], None] | None = None
) -> None:
    meeting = store.get(meeting_id)
    if not meeting or not meeting.segments:
        return
    if not summarizer.available():
        store.update(meeting_id, error="No summary yet: install and open Ollama, then click Generate summary.")
        return
    if on_progress:
        on_progress("Summarizing the meeting…")
    labels = meeting.to_dict()["speaker_labels"]
    try:
        title, summary = summarizer.summarize(meeting.segments, meeting.language, labels)
    except Exception as exc:
        log.exception("Meeting summary failed")
        store.update(meeting_id, error=f"The summary failed: {exc}")
        return
    store.update(meeting_id, summary=summary, title=title or meeting.title, error=None)
