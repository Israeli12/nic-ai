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
