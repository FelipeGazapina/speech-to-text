"""Meeting notes (the Notetaker), stored next to the dictation history in history.db.

A meeting is recorded as two tracks, your microphone ("me") and the computer's audio ("others"),
kept as temporary raw audio files only until they're transcribed. What stays is the transcript
(timestamped, per speaker) and its summary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .history import DB_PATH, open_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id               INTEGER PRIMARY KEY,
    started_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    duration_seconds REAL,
    title            TEXT,
    language         TEXT,
    status           TEXT NOT NULL DEFAULT 'recording',  -- recording, processing, done, failed
    system_audio     INTEGER NOT NULL DEFAULT 1,         -- 0: the computer's audio couldn't be recorded
    segments         TEXT,                               -- JSON: [{"start", "end", "speaker", "text"}]
    summary          TEXT,                               -- Markdown
    error            TEXT,
    audio_dir        TEXT                                -- temporary audio; NULL once transcribed
);
"""

SPEAKER_LABELS = {
    "pt": {"me": "Você", "others": "Outros"},
    "en": {"me": "You", "others": "Others"},
}


@dataclass
class Segment:
    start: float
    end: float
    speaker: str  # "me" or "others"
    text: str


@dataclass
class Meeting:
    id: int
    started_at: str
    duration_seconds: float | None
    title: str | None
    language: str | None
    status: str
    system_audio: bool
    summary: str | None
    error: str | None
    audio_dir: str | None
    segments: list[Segment] = field(default_factory=list)

    @property
    def display_title(self) -> str:
        return self.title or f"Meeting {self.started_at[:16]}"

    def transcript_text(self) -> str:
        labels = SPEAKER_LABELS.get(self.language or "", SPEAKER_LABELS["en"])
        return "\n".join(
            f"[{format_timestamp(s.start)}] {labels.get(s.speaker, s.speaker)}: {s.text}" for s in self.segments
        )

    def to_dict(self, with_segments: bool = True) -> dict:
        data = {
            "id": self.id,
            "started_at": self.started_at,
            "duration_seconds": self.duration_seconds,
            "title": self.display_title,
            "language": self.language,
            "status": self.status,
            "system_audio": self.system_audio,
            "has_summary": bool(self.summary),
            "error": self.error,
        }
        if with_segments:
            data["summary"] = self.summary
            data["segments"] = [s.__dict__ for s in self.segments]
            data["speaker_labels"] = SPEAKER_LABELS.get(self.language or "", SPEAKER_LABELS["en"])
            data["transcript_text"] = self.transcript_text()
        return data


def format_timestamp(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"


class MeetingStore:
    def __init__(self, path: Path | None = None):
        self.path = path = path or DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self.temp_dir = path.parent / "meeting-audio"
        with open_db(path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    def start(self, audio_dir: Path, system_audio: bool) -> int:
        with open_db(self.path) as db:
            cursor = db.execute(
                "INSERT INTO meetings (audio_dir, system_audio) VALUES (?, ?)", (str(audio_dir), int(system_audio))
            )
            return int(cursor.lastrowid)

    def update(self, meeting_id: int, **fields) -> None:
        allowed = {"duration_seconds", "title", "language", "status", "system_audio", "summary", "error", "audio_dir"}
        unknown = set(fields) - allowed - {"segments"}
        if unknown:
            raise ValueError(f"Unknown meeting fields: {unknown}")
        if "segments" in fields:
            fields["segments"] = json.dumps([s.__dict__ for s in fields["segments"]], ensure_ascii=False)
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with open_db(self.path) as db:
            db.execute(f"UPDATE meetings SET {assignments} WHERE id = ?", (*fields.values(), meeting_id))

    def get(self, meeting_id: int) -> Meeting | None:
        with open_db(self.path) as db:
            row = db.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return _to_meeting(row) if row else None

    def list(self, limit: int = 200) -> list[Meeting]:
        with open_db(self.path) as db:
            rows = db.execute("SELECT * FROM meetings ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_to_meeting(row, with_segments=False) for row in rows]

    def unfinished(self) -> list[Meeting]:
        """Meetings interrupted while recording or processing (e.g. the app quit): they can be resumed."""
        with open_db(self.path) as db:
            rows = db.execute("SELECT * FROM meetings WHERE status IN ('recording', 'processing')").fetchall()
        return [_to_meeting(row) for row in rows]

    def delete(self, meeting_id: int) -> None:
        with open_db(self.path) as db:
            db.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))


def _to_meeting(row, with_segments: bool = True) -> Meeting:
    segments = []
    if with_segments and row["segments"]:
        segments = [Segment(**item) for item in json.loads(row["segments"])]
    return Meeting(
        id=row["id"],
        started_at=row["started_at"],
        duration_seconds=row["duration_seconds"],
        title=row["title"],
        language=row["language"],
        status=row["status"],
        system_audio=bool(row["system_audio"]),
        summary=row["summary"],
        error=row["error"],
        audio_dir=row["audio_dir"],
        segments=segments,
    )
