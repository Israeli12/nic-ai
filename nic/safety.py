"""Guard rails between the model and your actual devices."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .config import Config, expand
from .tools.registry import ConfirmationRequired, Tool, ToolError, ToolRegistry

# Callback that asks the human; returns True to allow the action.
Approver = Callable[[str, dict[str, Any], str], bool]


def describe_action(tool: Tool, arguments: dict[str, Any]) -> str:
    """A one-line, human-readable summary used in confirmation prompts."""
    if arguments:
        rendered = ", ".join(f"{key}={value!r}" for key, value in sorted(arguments.items()))
        return f"{tool.name}({rendered})"
    return f"{tool.name}()"


class Guard:
    """Applies the blocklist, confirmation policy, and audit log."""

    def __init__(self, config: Config, approver: Approver | None = None):
        self.config = config
        self.approver = approver
        self._log_path = expand(config.safety.audit_log)

    def set_approver(self, approver: Approver | None) -> None:
        self.approver = approver

    def execute(self, registry: ToolRegistry, name: str, arguments: dict[str, Any]) -> Any:
        tool = registry.get(name)
        if name in self.config.safety.blocked_tools:
            raise ToolError(f"{name} is blocked by your config (safety.blocked_tools)")

        summary = describe_action(tool, arguments)
        if tool.dangerous and self.config.safety.confirm_dangerous_actions:
            if self.approver is None:
                # No one is around to say yes, so the safe answer is no.
                raise ConfirmationRequired(name, arguments, summary)
            if not self.approver(name, arguments, summary):
                self.audit(name, arguments, "denied")
                raise ToolError(f"user declined: {summary}")

        try:
            result = tool.call(arguments)
        except Exception as exc:
            self.audit(name, arguments, f"error: {exc}")
            raise
        self.audit(name, arguments, "ok")
        return result

    def audit(self, name: str, arguments: dict[str, Any], outcome: str) -> None:
        entry = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tool": name,
            "arguments": arguments,
            "outcome": outcome,
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError:
            # An unwritable audit log must not stop you controlling your devices.
            pass


def pending_approval_token(name: str, arguments: dict[str, Any]) -> str:
    """Stable id for an action awaiting approval in the web UI."""
    payload = json.dumps({"tool": name, "arguments": arguments}, sort_keys=True)
    return str(abs(hash(payload)))
