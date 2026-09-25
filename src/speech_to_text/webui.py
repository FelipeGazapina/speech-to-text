"""The app's window: a small web UI (Notetaker + Dictations tabs) served only to this Mac.

It listens on 127.0.0.1 alone, and every API call must carry a random token created at launch,
so neither other machines nor web pages open in your browser can read your notes.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from .web_page import PAGE

log = logging.getLogger(__name__)


class Backend(Protocol):
    def state(self) -> dict: ...
    def list_meetings(self) -> list[dict]: ...
    def get_meeting(self, meeting_id: int) -> dict | None: ...
    def start_meeting(self) -> dict: ...
    def stop_meeting(self) -> dict: ...
    def summarize_meeting(self, meeting_id: int) -> dict: ...
    def rename_meeting(self, meeting_id: int, title: str) -> dict: ...
    def delete_meeting(self, meeting_id: int) -> dict: ...
    def list_dictations(self, search: str | None, limit: int) -> list[dict]: ...
    def copy(self, text: str) -> dict: ...


class WebUI:
    def __init__(self, backend: Backend):
        self.backend = backend
        self.token = secrets.token_urlsafe(24)
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("WebUI.start() first")
        return f"http://127.0.0.1:{self._server.server_port}/#token={self.token}"

    def start(self) -> str:
        if self._server is None:
            handler = type("Handler", (_Handler,), {"backend": self.backend, "token": self.token})
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=self._server.serve_forever, name="webui", daemon=True).start()
            log.info("Window UI on port %d", self._server.server_port)
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None


class _Handler(BaseHTTPRequestHandler):
    backend: Backend
    token: str

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            return
        if not self._authorized():
            return
        query = parse_qs(url.query)
        routes = {
            r"/api/state": lambda: self.backend.state(),
            r"/api/meetings": lambda: self.backend.list_meetings(),
            r"/api/meetings/(\d+)": lambda id_: self.backend.get_meeting(int(id_)),
            r"/api/dictations": lambda: self.backend.list_dictations(
                (query.get("q") or [None])[0], int((query.get("limit") or ["200"])[0])
            ),
        }
        self._route(routes, url.path)

    def do_POST(self) -> None:
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": "Invalid JSON"})
            return
        routes = {
            r"/api/meetings/start": lambda: self.backend.start_meeting(),
            r"/api/meetings/stop": lambda: self.backend.stop_meeting(),
            r"/api/meetings/(\d+)/summarize": lambda id_: self.backend.summarize_meeting(int(id_)),
            r"/api/meetings/(\d+)/rename": lambda id_: self.backend.rename_meeting(int(id_), str(body.get("title", ""))),
            r"/api/meetings/(\d+)/delete": lambda id_: self.backend.delete_meeting(int(id_)),
            r"/api/copy": lambda: self.backend.copy(str(body.get("text", ""))),
        }
        self._route(routes, urlparse(self.path).path)

    def _route(self, routes: dict, path: str) -> None:
        for pattern, action in routes.items():
            match = re.fullmatch(pattern, path)
            if match:
                try:
                    result = action(*match.groups())
                except Exception as exc:
                    log.exception("Window UI request failed: %s", path)
                    self._json(500, {"error": str(exc)})
                    return
                if result is None:
                    self._json(404, {"error": "Not found"})
                else:
                    self._json(200, result)
                return
        self._json(404, {"error": "Not found"})

    def _authorized(self) -> bool:
        # A custom header can't be sent cross-site without a CORS preflight, which we never approve.
        if secrets.compare_digest(self.headers.get("X-Token", ""), self.token):
            return True
        self._json(403, {"error": "Forbidden"})
        return False

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass
