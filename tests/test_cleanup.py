import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from speech_to_text.cleanup import LLMCleaner, basic_cleanup, is_plausible_cleanup, strip_model_output
from speech_to_text.config import CleanupConfig


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Um, so I think uh we should ship it.", "So I think we should ship it."),
        ("  Hello   world ,", "Hello world,"),
        ("Thank you.", ""),
        ("you", ""),
        ("Thank you for the review, merging now.", "Thank you for the review, merging now."),
        ("npm install failed", "npm install failed"),
        ("Um, useState is fine", "useState is fine"),
        ("Hmm, the umbrella struct", "The umbrella struct"),
    ],
)
def test_basic_cleanup(raw, expected):
    assert basic_cleanup(raw) == expected


def test_um_is_a_word_in_portuguese():
    assert basic_cleanup("Uh, cria um branch novo.", language="pt") == "Cria um branch novo."
    assert basic_cleanup("Um, create a branch.", language="en") == "Create a branch."
    assert basic_cleanup("Obrigado.", language="pt") == ""


def test_replacements_are_whole_word_and_case_insensitive():
    replacements = {"cube control": "kubectl", "get hub": "GitHub"}
    assert basic_cleanup("Run Cube Control apply, then push to get hub.", replacements) == (
        "Run kubectl apply, then push to GitHub."
    )
    assert basic_cleanup("forget hubris", replacements) == "forget hubris"


def test_strip_model_output():
    assert strip_model_output("<think>hmm</think>\n\"Hello.\"") == "Hello."
    assert strip_model_output("<transcript>\nHi\n</transcript>") == "Hi"


def test_plausibility_guard_rejects_answers():
    raw = "can you check why the build is failing"
    answer = "Sure! The build is failing because " + "x" * 200
    assert not is_plausible_cleanup(raw, answer)
    assert is_plausible_cleanup(raw, "Can you check why the build is failing?")
    assert not is_plausible_cleanup(raw, "")


class _FakeOllama(BaseHTTPRequestHandler):
    reply = "Cleaned text."
    last_payload: dict = {}

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models": []}')

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).last_payload = json.loads(body)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"message": {"content": type(self).reply}}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def ollama():
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_llm_cleaner_round_trip(ollama):
    cleaner = LLMCleaner(CleanupConfig(ollama_url=ollama, vocabulary=["Acme"]))
    assert cleaner.clean("cleaned text um") == "Cleaned text."
    payload = _FakeOllama.last_payload
    assert payload["stream"] is False
    assert payload["messages"][0]["role"] == "system"
    assert "Acme" in payload["messages"][0]["content"]
    assert "cleaned text um" in payload["messages"][-1]["content"]


def test_llm_cleaner_falls_back_when_ollama_is_down():
    cleaner = LLMCleaner(CleanupConfig(ollama_url="http://127.0.0.1:9"))
    assert cleaner.clean("hello") is None


def test_llm_cleaner_disabled():
    assert LLMCleaner(CleanupConfig(enabled=False)).clean("hello") is None


class _FakeOllamaWithoutModel(_FakeOllama):
    pulled: list = []

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        models = [{"name": "qwen2.5:3b"}] if type(self).pulled else []
        self.wfile.write(json.dumps({"models": models}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).pulled.append(body["model"])
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "success"}')


def test_cleaner_downloads_its_model_through_ollama():
    server = HTTPServer(("127.0.0.1", 0), _FakeOllamaWithoutModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cleaner = LLMCleaner(CleanupConfig(ollama_url=f"http://127.0.0.1:{server.server_port}"))
        assert cleaner.setup_state() == "missing_model"
        cleaner.download_model()
        assert _FakeOllamaWithoutModel.pulled == ["qwen2.5:3b"]
        assert cleaner.setup_state() == "ready"
    finally:
        server.shutdown()
    assert LLMCleaner(CleanupConfig(ollama_url="http://127.0.0.1:9")).setup_state() == "no_ollama"
    assert LLMCleaner(CleanupConfig(enabled=False)).setup_state() == "disabled"
