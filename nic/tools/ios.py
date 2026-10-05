"""iPhone bridge.

Apple exposes no offline device-control API, so this talks to a Shortcut
you host yourself: the phone runs a Shortcuts automation that listens on
your LAN and performs named actions. Compared with Android this is
deliberately shallow - it is the ceiling iOS allows, not a design choice.
See docs/ios.md for the Shortcut setup.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..config import Config
from .registry import ToolError, ToolRegistry, object_schema, string_param


def build_registry(config: Config) -> ToolRegistry:
    registry = ToolRegistry()
    ios = config.ios

    def post(action: str, payload: dict[str, object]) -> str:
        if not ios.bridge_url:
            raise ToolError(
                "No iPhone bridge configured. Set ios.bridge_url in config.yaml"
                " after creating the Shortcut described in docs/ios.md"
            )
        body = json.dumps({"action": action, "secret": ios.shared_secret, **payload}).encode()
        request = urllib.request.Request(
            ios.bridge_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=ios.request_timeout_seconds) as response:
                return response.read().decode("utf-8", "replace").strip() or "ok"
        except urllib.error.URLError as exc:
            raise ToolError(
                f"could not reach the iPhone bridge at {ios.bridge_url}: {exc.reason}."
                " Is the phone awake and on the same Wi-Fi?"
            ) from exc

    @registry.tool(
        "ios_run_shortcut",
        "Run a named Shortcut on the iPhone, optionally passing text input."
        " The Shortcut must already exist on the phone.",
        object_schema(
            {
                "name": string_param("Exact Shortcut name as it appears on the iPhone"),
                "input_text": string_param("Optional text passed to the Shortcut"),
            },
            ["name"],
        ),
        surface="ios",
    )
    def ios_run_shortcut(name: str, input_text: str = "") -> str:
        return post("run_shortcut", {"name": name, "input": input_text})

    @registry.tool(
        "ios_status",
        "Ask the iPhone bridge for battery level and connectivity.",
        surface="ios",
    )
    def ios_status() -> str:
        return post("status", {})

    @registry.tool(
        "ios_notify",
        "Show a notification on the iPhone.",
        object_schema(
            {
                "title": string_param("Notification title"),
                "body": string_param("Notification body text"),
            },
            ["title"],
        ),
        surface="ios",
    )
    def ios_notify(title: str, body: str = "") -> str:
        return post("notify", {"title": title, "body": body})

    return registry
