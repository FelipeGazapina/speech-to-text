import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from speech_to_text.config import CleanupConfig
from speech_to_text.meeting_notes import (
    Summarizer,
    _chunk_lines,
    merge_tracks,
    parse_summary,
    process_meeting,
    speech_regions,
    summarize_meeting,
)
from speech_to_text.meetings import MeetingStore, Segment, format_timestamp

RATE = 16_000


def tone(seconds: float, amplitude: float = 0.1) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def test_speech_regions_skip_silence_and_merge_short_gaps():
    audio = np.concatenate([silence(2), tone(1.5), silence(0.4), tone(1), silence(3), tone(0.1), silence(2), tone(2)])
    regions = [(round(s / RATE, 1), round(e / RATE, 1)) for s, e in speech_regions(audio)]
    # 0.4 s pause merged into one region; the 0.1 s blip dropped; padding of 0.25 s around each.
    assert len(regions) == 2
    assert regions[0][0] == pytest.approx(1.75, abs=0.1) and regions[0][1] == pytest.approx(5.15, abs=0.1)
    assert regions[1][0] == pytest.approx(9.75, abs=0.1)
    assert len(speech_regions(tone(3))) == 1  # all speech, no silence to measure the noise floor on
    assert speech_regions(silence(5)) == []


def test_merge_tracks_drops_microphone_echo_of_the_other_people():
    others = [Segment(10.0, 13.0, "others", "Can you share the dashboard numbers?")]
    mine = [
        Segment(10.3, 13.1, "me", "can you share the dashboard numbers"),  # speakers leaking into the mic
        Segment(14.0, 16.0, "me", "Sure, one second."),
        Segment(2.0, 4.0, "me", "Can you share the dashboard numbers?"),  # same words, but at another time
    ]
    merged = merge_tracks(mine, others)
    assert [(s.speaker, s.start) for s in merged] == [("me", 2.0), ("others", 10.0), ("me", 14.0)]


def test_parse_summary_and_chunks():
    assert parse_summary("# Sprint planning\n\n## Summary\nWe planned.") == ("Sprint planning", "## Summary\nWe planned.")
    assert parse_summary("No heading here") == ("", "No heading here")
    lines = [f"line {i} " + "word " * 10 for i in range(10)]
    chunks = _chunk_lines(lines, max_words=30)
    assert len(chunks) == 5 and "\n".join(chunks).split("\n") == lines
    assert format_timestamp(75) == "01:15" and format_timestamp(3725) == "1:02:05"


class FakeTranscriber:
    def __init__(self, texts: dict[str, list[str]], languages: dict[str, str]):
        self.texts, self.languages = texts, languages
        self.current = None

    def detect_language(self, audio):
        return self.languages[self.current]

    def transcribe_segments(self, audio, language, prompt=None):
        return [(0.0, len(audio) / RATE, self.texts[self.current].pop(0))]


def write_pcm(path, audio):
    (np.clip(audio, -1, 1) * 32767).astype("<i2").tofile(path)


@pytest.fixture
def store(tmp_path):
    return MeetingStore(tmp_path / "history.db")


def test_process_meeting_transcribes_both_tracks_and_deletes_the_audio(store, tmp_path, monkeypatch):
    folder = tmp_path / "meeting-audio" / "1"
    folder.mkdir(parents=True)
    write_pcm(folder / "me.pcm", np.concatenate([silence(1), tone(2), silence(6)]))
    write_pcm(folder / "others.pcm", np.concatenate([silence(4), tone(3), silence(2)]))
    meeting_id = store.start(folder, system_audio=True)

    transcriber = FakeTranscriber(
        {"me": ["Bom dia, pessoal."], "others": ["Bom dia! Vamos começar pelo deploy de sexta."]},
        {"me": "pt", "others": "pt"},
    )
    # Tell the fake which track is being transcribed.
    import speech_to_text.meeting_notes as notes

    real = notes.transcribe_track

    def tracking(transcriber_, audio, speaker, *args, **kwargs):
        transcriber_.current = speaker
        return real(transcriber_, audio, speaker, *args, **kwargs)

    monkeypatch.setattr(notes, "transcribe_track", tracking)
    progress = []
    meeting = process_meeting(meeting_id, store, transcriber, summarizer=None, on_progress=progress.append)

    assert meeting.status == "done" and meeting.language == "pt"
    assert [(s.speaker, s.text) for s in meeting.segments] == [
        ("me", "Bom dia, pessoal."),
        ("others", "Bom dia! Vamos começar pelo deploy de sexta."),
    ]
    assert meeting.segments[0].start == pytest.approx(0.75, abs=0.05)
    assert meeting.duration_seconds == pytest.approx(9, abs=0.1)
    assert not folder.exists() and meeting.audio_dir is None
    assert "Você: Bom dia, pessoal." in meeting.transcript_text()
    assert progress[-1].endswith("100%")


def test_failed_transcription_keeps_the_audio_for_a_retry(store, tmp_path):
    folder = tmp_path / "a"
    folder.mkdir()
    write_pcm(folder / "me.pcm", tone(2))
    meeting_id = store.start(folder, system_audio=False)

    class Broken:
        def detect_language(self, audio):
            raise RuntimeError("GPU hiccup")

    with pytest.raises(RuntimeError):
        process_meeting(meeting_id, store, Broken(), summarizer=None)
    meeting = store.get(meeting_id)
    assert meeting.status == "failed" and "GPU hiccup" in meeting.error
    assert (folder / "me.pcm").exists()
    assert [m.id for m in store.unfinished()] == []  # failed ones aren't auto-resumed


class _FakeOllama(BaseHTTPRequestHandler):
    requests: list = []

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models": []}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(body)
        system = body["messages"][0]["content"]
        reply = "- point" if system.startswith("You take notes") else "# Deploy de sexta\n\n## Resumo\nCombinamos o deploy."
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"message": {"content": reply}}).encode())

    def log_message(self, *args):
        pass


def test_summary_uses_ollama_and_splits_long_meetings(store, tmp_path, monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        summarizer = Summarizer(CleanupConfig(ollama_url=f"http://127.0.0.1:{server.server_port}"))
        meeting_id = store.start(tmp_path, system_audio=True)
        store.update(meeting_id, language="pt", status="done",
                     segments=[Segment(i * 5.0, i * 5.0 + 4, "others", "palavra " * 400) for i in range(10)])

        summarize_meeting(meeting_id, store, summarizer)
        meeting = store.get(meeting_id)
        assert meeting.title == "Deploy de sexta" and meeting.summary == "## Resumo\nCombinamos o deploy."
        systems = [r["messages"][0]["content"] for r in _FakeOllama.requests]
        assert sum(s.startswith("You take notes") for s in systems) == 2  # 4000 words -> 2 parts
        assert "Brazilian Portuguese" in systems[-1] and "## Próximos passos" in systems[-1]
        assert "Outros: palavra" in _FakeOllama.requests[0]["messages"][1]["content"]
    finally:
        server.shutdown()


def test_summary_without_ollama_leaves_a_hint(store, tmp_path):
    meeting_id = store.start(tmp_path, system_audio=True)
    store.update(meeting_id, segments=[Segment(0, 1, "me", "oi")], status="done")
    summarize_meeting(meeting_id, store, Summarizer(CleanupConfig(ollama_url="http://127.0.0.1:9")))
    assert "Ollama" in store.get(meeting_id).error


def test_store_lists_updates_and_deletes(store, tmp_path):
    first = store.start(tmp_path / "x", system_audio=True)
    second = store.start(tmp_path / "y", system_audio=False)
    store.update(second, title="Standup", status="done", duration_seconds=62)
    assert [m.id for m in store.list()] == [second, first]
    assert store.list()[0].to_dict(with_segments=False)["title"] == "Standup"
    assert store.get(first).display_title.startswith("Meeting ")
    assert [m.id for m in store.unfinished()] == [first]
    with pytest.raises(ValueError):
        store.update(first, bogus=1)
    store.delete(first)
    assert store.get(first) is None
