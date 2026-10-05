import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from nic.config import Config
from nic.llm import ModelReply, ToolCall
from nic.tools.registry import ToolRegistry
from nic.web.server import Session, make_handler


class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)

    def chat(self, messages, tools=None):
        return self.replies.pop(0) if self.replies else ModelReply("done", [])


@pytest.fixture()
def server(tmp_path):
    config = Config()
    config.web.access_token = "test-token"
    config.safety.audit_log = str(tmp_path / "audit.log")

    registry = ToolRegistry()

    @registry.tool("ping", "Ping")
    def ping() -> str:
        return "pong"

    @registry.tool("nuke", "Dangerous", dangerous=True)
    def nuke() -> str:
        return "nuked"

    session = Session(config)
    session.agent.registry = registry
    session.agent.client = ScriptedClient(
        [
            ModelReply("", [ToolCall("ping", {})]),
            ModelReply("Pinged.", []),
            ModelReply("", [ToolCall("nuke", {})]),
            ModelReply("Nuked.", []),
        ]
    )

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, session))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def post(base, path, payload, token="test-token"):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Nic-Token"] = token
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def test_health_needs_no_token(server):
    with urllib.request.urlopen(server + "/api/health", timeout=10) as response:
        assert json.loads(response.read().decode())["ok"] is True


def test_ui_is_served(server):
    with urllib.request.urlopen(server + "/", timeout=10) as response:
        assert b"nic" in response.read()


def test_chat_without_token_is_rejected(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post(server, "/api/chat", {"message": "hi"}, token=None)
    assert excinfo.value.code == 401


def test_chat_with_wrong_token_is_rejected(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post(server, "/api/chat", {"message": "hi"}, token="nope")
    assert excinfo.value.code == 401


def test_chat_runs_tools_and_returns_steps(server):
    data = post(server, "/api/chat", {"message": "ping it"})
    assert data["reply"] == "Pinged."
    assert data["steps"][0]["tool"] == "ping"


def test_empty_message_is_rejected(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post(server, "/api/chat", {"message": "   "})
    assert excinfo.value.code == 400


def test_dangerous_action_requires_explicit_approval(server):
    post(server, "/api/chat", {"message": "ping it"})
    pending = post(server, "/api/chat", {"message": "nuke it"})["pending"]
    assert "nuke" in pending["summary"]
    approved = post(server, "/api/approve", {"token": pending["token"]})
    assert approved["steps"][0]["result"] == "nuked"


def test_stale_approval_token_is_handled(server):
    assert "expired" in post(server, "/api/approve", {"token": "bogus"})["reply"]


def test_unknown_endpoint_is_404(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post(server, "/api/launch-missiles", {})
    assert excinfo.value.code == 404


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return response.status, response.headers, response.read()


def test_manifest_describes_an_installable_app(server):
    status, headers, body = get(server, "/manifest.webmanifest")
    manifest = json.loads(body)
    assert status == 200
    assert headers["Content-Type"] == "application/manifest+json"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])


def test_home_screen_icons_are_served(server):
    for name in ("icon-192.png", "icon-512.png", "apple-touch-icon.png", "maskable-512.png"):
        status, headers, body = get(server, f"/icons/{name}")
        assert status == 200
        assert headers["Content-Type"] == "image/png"
        assert body.startswith(b"\x89PNG")


def test_index_links_the_manifest_and_ios_icon(server):
    body = get(server, "/")[1 + 1].decode()
    assert 'rel="manifest"' in body
    assert 'rel="apple-touch-icon"' in body
    assert 'name="apple-mobile-web-app-capable"' in body


def test_service_worker_is_served_uncached(server):
    status, headers, body = get(server, "/sw.js")
    assert status == 200
    assert headers["Cache-Control"] == "no-cache"
    assert b"/api/" in body


def test_unknown_icon_is_not_served(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(server, "/icons/../server.py")
    assert excinfo.value.code == 404
