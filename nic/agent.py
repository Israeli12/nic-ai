"""The agent loop: model proposes tool calls, the guard runs them."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Config
from .llm import ModelReply, OllamaClient, ToolCall
from .safety import Approver, Guard
from .tools.registry import ConfirmationRequired, ToolError, ToolRegistry

SYSTEM_PROMPT = """You are Nic, a private assistant that runs entirely offline on the user's \
Windows laptop and controls their laptop and phone through the tools provided.

Rules:
- Prefer calling a tool over describing what the user could do themselves.
- Call one tool at a time and read its result before deciding the next step.
- Screen coordinates are unknown until you take a screenshot; never guess taps blindly.
- If a tool reports an error, explain it plainly and suggest the fix; do not retry blindly.
- If no tool fits the request, say so in one sentence.
- Keep replies short and concrete. The user hears many of them read aloud."""


def build_registry(config: Config) -> ToolRegistry:
    """Assemble every enabled tool surface into one registry."""
    from .tools import android, ios, laptop

    registry = ToolRegistry()
    if config.laptop.enabled:
        registry.merge(laptop.build_registry(config))
    if config.android.enabled:
        registry.merge(android.build_registry(config))
    if config.ios.enabled:
        registry.merge(ios.build_registry(config))
    registry.drop(config.safety.blocked_tools)
    return registry


@dataclass
class Step:
    """One tool invocation, for display in the UI and the CLI."""

    tool: str
    arguments: dict[str, Any]
    result: str
    ok: bool = True


@dataclass
class Turn:
    reply: str
    steps: list[Step] = field(default_factory=list)
    # Set when a dangerous action is waiting for the user to approve it.
    pending: ConfirmationRequired | None = None


class Agent:
    def __init__(
        self,
        config: Config,
        client: OllamaClient | None = None,
        registry: ToolRegistry | None = None,
        approver: Approver | None = None,
        on_step: Callable[[Step], None] | None = None,
    ):
        self.config = config
        self.client = client or OllamaClient(config.model)
        self.registry = registry if registry is not None else build_registry(config)
        self.guard = Guard(config, approver)
        self.on_step = on_step
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def reset(self) -> None:
        self.messages = self.messages[:1]

    def _record_step(self, step: Step) -> None:
        if self.on_step:
            self.on_step(step)

    def _run_tool(self, call: ToolCall) -> Step:
        try:
            result = self.guard.execute(self.registry, call.name, call.arguments)
        except ConfirmationRequired:
            # Must reach ask(), which hands the decision to the user.
            raise
        except ToolError as exc:
            return Step(call.name, call.arguments, f"error: {exc}", ok=False)
        except Exception as exc:  # noqa: BLE001 - surface any tool crash to the model
            return Step(call.name, call.arguments, f"error: {exc.__class__.__name__}: {exc}", ok=False)
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str)
        return Step(call.name, call.arguments, result)

    def ask(self, user_text: str) -> Turn:
        """Run one user turn to completion, executing tools as needed."""
        self.messages.append({"role": "user", "content": user_text})
        steps: list[Step] = []

        for _ in range(self.config.model.max_tool_iterations):
            reply: ModelReply = self.client.chat(self.messages, self.registry.schemas())
            assistant_message: dict[str, Any] = {"role": "assistant", "content": reply.text}
            if reply.tool_calls:
                assistant_message["tool_calls"] = [
                    {"function": {"name": call.name, "arguments": call.arguments}}
                    for call in reply.tool_calls
                ]
            self.messages.append(assistant_message)

            if not reply.tool_calls:
                return Turn(reply=reply.text or "(no reply)", steps=steps)

            for call in reply.tool_calls:
                try:
                    step = self._run_tool(call)
                except ConfirmationRequired as pending:
                    # Hand control back to the frontend to ask the human.
                    return Turn(
                        reply=f"This needs your approval: {pending.summary}",
                        steps=steps,
                        pending=pending,
                    )
                steps.append(step)
                self._record_step(step)
                self.messages.append(
                    {"role": "tool", "name": call.name, "content": step.result}
                )

        return Turn(
            reply="I stopped after too many tool steps. Tell me the next single step to take.",
            steps=steps,
        )

    def run_approved(self, tool_name: str, arguments: dict[str, Any]) -> Turn:
        """Execute a previously pending dangerous action, then continue the turn."""
        tool = self.registry.get(tool_name)
        try:
            result = tool.call(arguments)
        except ToolError as exc:
            step = Step(tool_name, arguments, f"error: {exc}", ok=False)
        else:
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False, default=str)
            step = Step(tool_name, arguments, result)
        self.guard.audit(tool_name, arguments, "approved")
        self._record_step(step)
        self.messages.append({"role": "tool", "name": tool_name, "content": step.result})
        reply = self.client.chat(self.messages, self.registry.schemas())
        self.messages.append({"role": "assistant", "content": reply.text})
        return Turn(reply=reply.text or step.result, steps=[step])
