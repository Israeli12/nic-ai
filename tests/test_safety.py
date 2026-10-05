import json

import pytest

from nic.config import Config
from nic.safety import Guard, describe_action, pending_approval_token
from nic.tools.registry import ConfirmationRequired, ToolError, ToolRegistry


@pytest.fixture()
def registry() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.tool("safe_thing", "Harmless")
    def safe_thing() -> str:
        return "done"

    @registry.tool("risky_thing", "Dangerous", dangerous=True)
    def risky_thing() -> str:
        return "boom"

    return registry


@pytest.fixture()
def config(tmp_path) -> Config:
    config = Config()
    config.safety.audit_log = str(tmp_path / "audit.log")
    return config


def test_safe_tool_runs_without_approval(config, registry):
    assert Guard(config).execute(registry, "safe_thing", {}) == "done"


def test_dangerous_tool_without_approver_raises(config, registry):
    with pytest.raises(ConfirmationRequired) as excinfo:
        Guard(config).execute(registry, "risky_thing", {})
    assert excinfo.value.tool_name == "risky_thing"


def test_dangerous_tool_runs_once_approved(config, registry):
    guard = Guard(config, approver=lambda *_: True)
    assert guard.execute(registry, "risky_thing", {}) == "boom"


def test_declined_dangerous_tool_is_refused(config, registry):
    guard = Guard(config, approver=lambda *_: False)
    with pytest.raises(ToolError, match="user declined"):
        guard.execute(registry, "risky_thing", {})


def test_confirmation_can_be_switched_off(config, registry):
    config.safety.confirm_dangerous_actions = False
    assert Guard(config).execute(registry, "risky_thing", {}) == "boom"


def test_blocked_tools_are_refused_even_with_approver(config, registry):
    config.safety.blocked_tools = ["risky_thing"]
    guard = Guard(config, approver=lambda *_: True)
    with pytest.raises(ToolError, match="blocked"):
        guard.execute(registry, "risky_thing", {})


def test_audit_log_records_outcomes(config, registry, tmp_path):
    guard = Guard(config)
    guard.execute(registry, "safe_thing", {})
    entries = [
        json.loads(line)
        for line in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()
    ]
    assert entries[-1]["tool"] == "safe_thing"
    assert entries[-1]["outcome"] == "ok"


def test_describe_action_is_readable(registry):
    tool = registry.get("safe_thing")
    assert describe_action(tool, {}) == "safe_thing()"
    assert describe_action(tool, {"x": 1}) == "safe_thing(x=1)"


def test_pending_token_is_stable_per_action():
    first = pending_approval_token("t", {"a": 1, "b": 2})
    second = pending_approval_token("t", {"b": 2, "a": 1})
    assert first == second
    assert first != pending_approval_token("t", {"a": 2})
