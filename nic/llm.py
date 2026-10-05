"""Local model client.

We speak Ollama's HTTP API over loopback, so no request ever leaves the
machine. Only the standard library is used for the transport, which keeps
the install light on a CPU-only laptop.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import ModelConfig


class ModelUnavailable(RuntimeError):
    """Ollama is not running, or the requested model is not pulled."""


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelReply:
    text: str
    tool_calls: list[ToolCall]


class OllamaClient:
    def __init__(self, config: ModelConfig):
        self.config = config

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.config.host.rstrip('/')}{path}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code == 404:
                raise ModelUnavailable(
                    f"Ollama does not have '{self.config.name}'."
                    f" Run: ollama pull {self.config.name}"
                ) from exc
            raise ModelUnavailable(f"Ollama error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ModelUnavailable(
                f"Could not reach Ollama at {self.config.host} ({exc.reason})."
                " Start it with 'ollama serve'."
            ) from exc

    def available_models(self) -> list[str]:
        url = f"{self.config.host.rstrip('/')}/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode())
        except urllib.error.URLError as exc:
            raise ModelUnavailable(
                f"Could not reach Ollama at {self.config.host} ({exc.reason})."
                " Start it with 'ollama serve'."
            ) from exc
        return [entry.get("name", "") for entry in data.get("models", [])]

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        payload: dict[str, Any] = {
            "model": self.config.name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
                "num_ctx": self.config.context_tokens,
            },
        }
        if tools:
            payload["tools"] = tools
        data = self._post("/api/chat", payload)
        return parse_reply(data)


def parse_reply(data: dict[str, Any]) -> ModelReply:
    """Turn an Ollama /api/chat response into a :class:`ModelReply`."""
    message = data.get("message") or {}
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            # Some models hand back a JSON string instead of an object.
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(ToolCall(name=name, arguments=arguments))
    return ModelReply(text=(message.get("content") or "").strip(), tool_calls=calls)
