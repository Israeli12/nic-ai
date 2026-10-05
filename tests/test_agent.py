import pytest

from nic.agent import Agent
from nic.config import Config
from nic.llm import ModelReply, ToolCall
from nic.tools.registry import ToolRegistry


class ScriptedClient:
    """Stands in for Ollama: returns queued replies and records the messages."""

    def __init__(self, replies: list[ModelReply]):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.seen.append([dict(message) for message in messages])
        if not self.replies:
            return ModelReply(text="done", tool_calls=[])
        return self.replies.pop(0)


@pytest.fixture()
def config(tmp_path) -> Config:
    config = Config()
    config.safety.audit_log = str(tmp_path / "audit.log")
    return config


@pytest.fixture()
def registry() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.tool("battery", "Battery level")
    def battery() -> dict:
        return {"percent": 73}

    @registry.tool("explode", "Fails")
    def explode() -> str:
        raise RuntimeError("cable unplugged")

    @registry.tool("wipe", "Dangerous", dangerous=True)
    def wipe() -> str:
        return "wiped"

    return registry


def agent_with(config, registry, replies, **kwargs) -> Agent:
    return Agent(config, client=ScriptedClient(replies), registry=registry, **kwargs)


def test_plain_reply_passes_through(config, registry):
    agent = agent_with(config, registry, [ModelReply("hi there", [])])
    turn = agent.ask("hello")
    assert turn.reply == "hi there"
    assert turn.steps == []


def test_tool_result_is_fed_back_to_the_model(config, registry):
    agent = agent_with(
        config,
        registry,
        [
            ModelReply("", [ToolCall("battery", {})]),
            ModelReply("Your phone is at 73 percent.", []),
        ],
    )
    turn = agent.ask("battery?")
    assert turn.reply == "Your phone is at 73 percent."
    assert turn.steps[0].tool == "battery"
    assert "73" in turn.steps[0].result
    # The tool result must be visible in the transcript for the second call.
    final_messages = agent.client.seen[-1]
    assert any(message["role"] == "tool" for message in final_messages)


def test_tool_crash_is_reported_not_raised(config, registry):
    agent = agent_with(
        config,
        registry,
        [ModelReply("", [ToolCall("explode", {})]), ModelReply("That failed.", [])],
    )
    turn = agent.ask("do it")
    assert turn.steps[0].ok is False
    assert "cable unplugged" in turn.steps[0].result


def test_unknown_tool_is_reported_to_the_model(config, registry):
    agent = agent_with(
        config,
        registry,
        [ModelReply("", [ToolCall("teleport", {})]), ModelReply("No such tool.", [])],
    )
    turn = agent.ask("teleport")
    assert turn.steps[0].ok is False
    assert "unknown tool" in turn.steps[0].result


def test_dangerous_tool_pauses_for_approval(config, registry):
    agent = agent_with(config, registry, [ModelReply("", [ToolCall("wipe", {})])])
    turn = agent.ask("wipe it")
    assert turn.pending is not None
    assert turn.pending.tool_name == "wipe"


def test_approved_action_runs_and_continues(config, registry):
    agent = agent_with(
        config,
        registry,
        [ModelReply("", [ToolCall("wipe", {})]), ModelReply("All done.", [])],
    )
    pending = agent.ask("wipe it").pending
    follow_up = agent.run_approved(pending.tool_name, pending.arguments)
    assert follow_up.steps[0].result == "wiped"
    assert follow_up.reply == "All done."


def test_approver_callback_allows_dangerous_tool_inline(config, registry):
    agent = agent_with(
        config,
        registry,
        [ModelReply("", [ToolCall("wipe", {})]), ModelReply("Wiped.", [])],
        approver=lambda *_: True,
    )
    turn = agent.ask("wipe it")
    assert turn.pending is None
    assert turn.steps[0].result == "wiped"


def test_tool_loop_is_bounded(config, registry):
    config.model.max_tool_iterations = 3
    agent = agent_with(
        config,
        registry,
        [ModelReply("", [ToolCall("battery", {})]) for _ in range(5)],
    )
    turn = agent.ask("loop forever")
    assert len(turn.steps) == 3
    assert "too many tool steps" in turn.reply


def test_reset_keeps_only_the_system_prompt(config, registry):
    agent = agent_with(config, registry, [ModelReply("hi", [])])
    agent.ask("hello")
    agent.reset()
    assert len(agent.messages) == 1
    assert agent.messages[0]["role"] == "system"


def test_steps_are_streamed_to_the_callback(config, registry):
    seen = []
    agent = agent_with(
        config,
        registry,
        [ModelReply("", [ToolCall("battery", {})]), ModelReply("ok", [])],
        on_step=seen.append,
    )
    agent.ask("battery?")
    assert [step.tool for step in seen] == ["battery"]
