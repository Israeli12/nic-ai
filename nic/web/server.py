"""A small local web server for the phone-friendly UI.

Deliberately stdlib-only: no extra packages to install on a CPU-only
laptop, and nothing listening beyond your own network. A token is still
required because anything on your Wi-Fi can reach the port.
"""

from __future__ import annotations

import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..agent import Agent, Turn
from ..config import Config
from ..llm import ModelUnavailable
from ..safety import pending_approval_token

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY_BYTES = 64 * 1024

# Everything the home-screen app needs, mapped to its media type.
STATIC_ROUTES = {
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
}
ICON_FILES = {
    "icon-192.png",
    "icon-512.png",
    "maskable-192.png",
    "maskable-512.png",
    "apple-touch-icon.png",
    "favicon-32.png",
}


def _local_ip() -> str:
    """Best guess at the LAN address to open on the phone."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class Session:
    """One agent, shared by every browser tab, serialised by a lock."""

    def __init__(self, config: Config):
        self.config = config
        self.lock = threading.Lock()
        # Approval happens as a second HTTP request, so the agent itself
        # has no approver and raises ConfirmationRequired instead.
        self.agent = Agent(config, approver=None)
        self.pending: dict[str, dict[str, Any]] = {}

    def _turn_payload(self, turn: Turn) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reply": turn.reply,
            "steps": [
                {"tool": step.tool, "arguments": step.arguments, "result": step.result, "ok": step.ok}
                for step in turn.steps
            ],
        }
        if turn.pending is not None:
            token = pending_approval_token(turn.pending.tool_name, turn.pending.arguments)
            self.pending[token] = {
                "tool": turn.pending.tool_name,
                "arguments": turn.pending.arguments,
            }
            payload["pending"] = {"token": token, "summary": turn.pending.summary}
        return payload

    def ask(self, text: str) -> dict[str, Any]:
        with self.lock:
            return self._turn_payload(self.agent.ask(text))

    def approve(self, token: str) -> dict[str, Any]:
        with self.lock:
            action = self.pending.pop(token, None)
            if action is None:
                return {"reply": "That approval expired. Ask again if you still want it."}
            turn = self.agent.run_approved(action["tool"], action["arguments"])
            return self._turn_payload(turn)

    def reject(self, token: str) -> dict[str, Any]:
        with self.lock:
            self.pending.pop(token, None)
            return {"reply": "Cancelled."}

    def reset(self) -> dict[str, Any]:
        with self.lock:
            self.agent.reset()
            self.pending.clear()
            return {"reply": "Context cleared."}


def make_handler(config: Config, session: Session) -> type[BaseHTTPRequestHandler]:
    token = config.web.access_token

    class Handler(BaseHTTPRequestHandler):
        server_version = "nic-ai"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Keep the console readable; the audit log is the real record.
            return

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            supplied = self.headers.get("X-Nic-Token", "")
            return bool(token) and secrets.compare_digest(supplied, token)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/", "/index.html"}:
                self._serve_static("index.html", "text/html; charset=utf-8")
            elif path == "/api/health":
                self._send_json(200, {"ok": True, "model": config.model.name})
            elif path in STATIC_ROUTES:
                self._serve_static(*STATIC_ROUTES[path])
            elif path.startswith("/icons/") and path.count("/") == 2:
                name = path.rsplit("/", 1)[1]
                if name in ICON_FILES:
                    self._serve_static(f"icons/{name}", "image/png", cache_seconds=86400)
                else:
                    self._send_json(404, {"error": "not found"})
            else:
                self._send_json(404, {"error": "not found"})

        def _serve_static(self, name: str, content_type: str, cache_seconds: int = 0) -> None:
            file_path = STATIC_DIR / name
            if not file_path.exists():
                self._send_json(404, {"error": f"missing asset: {name}"})
                return
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if cache_seconds:
                self.send_header("Cache-Control", f"public, max-age={cache_seconds}")
            elif name == "sw.js":
                # A stale service worker would pin an old UI forever.
                self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorised():
                self._send_json(401, {"error": "bad or missing access token"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._send_json(413, {"error": "request too large"})
                return
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON"})
                return

            try:
                if self.path == "/api/chat":
                    text = str(data.get("message", "")).strip()
                    if not text:
                        self._send_json(400, {"error": "message is required"})
                        return
                    self._send_json(200, session.ask(text))
                elif self.path == "/api/approve":
                    self._send_json(200, session.approve(str(data.get("token", ""))))
                elif self.path == "/api/reject":
                    self._send_json(200, session.reject(str(data.get("token", ""))))
                elif self.path == "/api/reset":
                    self._send_json(200, session.reset())
                else:
                    self._send_json(404, {"error": "not found"})
            except ModelUnavailable as exc:
                self._send_json(503, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - never kill the server thread
                self._send_json(500, {"error": f"{exc.__class__.__name__}: {exc}"})

    return Handler


def serve(config: Config) -> int:
    if not config.web.access_token:
        print("Refusing to start: set web.access_token in config.yaml first.")
        print("Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(24))\"")
        return 1
    session = Session(config)
    handler = make_handler(config, session)
    httpd = ThreadingHTTPServer((config.web.host, config.web.port), handler)
    where = _local_ip() if config.web.host in {"0.0.0.0", ""} else config.web.host
    print(f"nic-ai web UI: http://{where}:{config.web.port}")
    print("Open that on your phone (same Wi-Fi) and paste your access token.")
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
