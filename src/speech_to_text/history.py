"""Every dictation, saved locally in SQLite: recover text after a failed paste, and learn from it.

The database lives in ~/Library/Application Support/speech-to-text/history.db and never
leaves the Mac. Browse it with `stt history`, or any SQLite tool.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import DATA_DIR

DB_PATH = DATA_DIR / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcriptions (
    id                 INTEGER PRIMARY KEY,
    created_at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    language           TEXT,
    raw_text           TEXT NOT NULL,  -- what Whisper heard
    final_text         TEXT NOT NULL,  -- what was pasted
    corrected_text     TEXT,           -- what you said it should have been (Fix last transcription)
    app_name           TEXT,           -- where it was pasted
    audio_seconds      REAL,
    transcribe_seconds REAL,
    cleanup_seconds    REAL,
    cleanup_used       INTEGER NOT NULL DEFAULT 0,
    pasted             INTEGER         -- NULL = not attempted yet, 0 = failed, 1 = pasted
);
CREATE INDEX IF NOT EXISTS transcriptions_created_at ON transcriptions (created_at);

-- Word-level fixes learned from your corrections: Whisper/LLM wrote `wrong`, you meant `right`.
CREATE TABLE IF NOT EXISTS corrections (
    wrong      TEXT NOT NULL,
    right      TEXT NOT NULL,
    count      INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (wrong, right)
);
"""


@dataclass
class Transcription:
    id: int
    created_at: str
    language: str | None
    raw_text: str
    final_text: str
    corrected_text: str | None
    app_name: str | None
    pasted: int | None

    @property
    def best_text(self) -> str:
        return self.corrected_text or self.final_text


class HistoryStore:
    """Opens a short-lived connection per call, so it's safe from any thread."""

    def __init__(self, path: Path | None = None):
        self.path = path = path or DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # --- writing ---

    def add(
        self,
        *,
        raw_text: str,
        final_text: str,
        language: str | None = None,
        app_name: str | None = None,
        audio_seconds: float | None = None,
        transcribe_seconds: float | None = None,
        cleanup_seconds: float | None = None,
        cleanup_used: bool = False,
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO transcriptions (raw_text, final_text, language, app_name, audio_seconds,
                                               transcribe_seconds, cleanup_seconds, cleanup_used)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (raw_text, final_text, language, app_name, audio_seconds, transcribe_seconds,
                 cleanup_seconds, int(cleanup_used)),
            )
            return int(cursor.lastrowid)

    def mark_pasted(self, transcription_id: int, pasted: bool) -> None:
        with self._connect() as db:
            db.execute("UPDATE transcriptions SET pasted = ? WHERE id = ?", (int(pasted), transcription_id))

    def set_correction(self, transcription_id: int, corrected_text: str, pairs: list[tuple[str, str]]) -> None:
        with self._connect() as db:
            db.execute("UPDATE transcriptions SET corrected_text = ? WHERE id = ?", (corrected_text, transcription_id))
        for wrong, right in pairs:
            self.add_correction(wrong, right)

    def add_correction(self, wrong: str, right: str, weight: int = 1) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO corrections (wrong, right, count) VALUES (?, ?, ?)
                   ON CONFLICT (wrong, right) DO UPDATE
                   SET count = count + excluded.count, updated_at = datetime('now', 'localtime')""",
                (wrong, right, weight),
            )

    def remove_correction(self, wrong: str) -> int:
        with self._connect() as db:
            return db.execute("DELETE FROM corrections WHERE lower(wrong) = lower(?)", (wrong,)).rowcount

    # --- reading ---

    def get(self, transcription_id: int) -> Transcription | None:
        rows = self._select("WHERE id = ?", (transcription_id,), limit=1)
        return rows[0] if rows else None

    def recent(self, limit: int = 10, search: str | None = None, language: str | None = None) -> list[Transcription]:
        clauses, params = [], []
        if search:
            clauses.append("(final_text LIKE ? OR corrected_text LIKE ? OR raw_text LIKE ?)")
            params += [f"%{search}%"] * 3
        if language:
            clauses.append("language = ?")
            params.append(language)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._select(where, tuple(params), limit=limit)

    def corrections(self, min_count: int = 1) -> list[tuple[str, str, int]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT wrong, right, count FROM corrections WHERE count >= ? ORDER BY count DESC, updated_at DESC",
                (min_count,),
            ).fetchall()
        return [(r["wrong"], r["right"], r["count"]) for r in rows]

    def corrected_examples(self, limit: int = 3) -> list[tuple[str, str]]:
        """(raw Whisper text, what you corrected it to): the strongest signal of how you want text written."""
        with self._connect() as db:
            rows = db.execute(
                """SELECT raw_text, corrected_text FROM transcriptions
                   WHERE corrected_text IS NOT NULL AND length(raw_text) < 400
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [(r["raw_text"], r["corrected_text"]) for r in rows]

    def language_counts(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT language, count(*) AS n FROM transcriptions WHERE language IS NOT NULL GROUP BY language"
            ).fetchall()
        return {r["language"]: r["n"] for r in rows}

    def stats(self) -> dict:
        with self._connect() as db:
            row = db.execute(
                """SELECT count(*) AS dictations, coalesce(sum(audio_seconds), 0) AS seconds,
                          coalesce(sum(length(final_text) - length(replace(final_text, ' ', '')) + 1), 0) AS words,
                          coalesce(sum(pasted = 0), 0) AS failed_pastes,
                          coalesce(sum(corrected_text IS NOT NULL), 0) AS corrected
                   FROM transcriptions"""
            ).fetchone()
        return dict(row)

    def _select(self, where: str, params: tuple, limit: int) -> list[Transcription]:
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT id, created_at, language, raw_text, final_text, corrected_text, app_name, pasted
                    FROM transcriptions {where} ORDER BY id DESC LIMIT ?""",
                (*params, limit),
            ).fetchall()
        return [Transcription(**dict(r)) for r in rows]
