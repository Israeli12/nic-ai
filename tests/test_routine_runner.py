from datetime import datetime, timedelta

import json

import pytest

from nic.config import Config
from nic.llm import ModelReply
from nic.schedule import Action, Routine, RoutineRunner, RoutineStore, parse_schedule
from nic.tools.registry import ToolRegistry
from nic.tools.routines import build_registry as build_routine_tools

MONDAY_10PM = datetime(2026, 10, 5, 22, 0)


class FakeClock:
    """A clock the tests move by hand, so nothing ever sleeps."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture()
def config(tmp_path) -> Config:
    config = Config()
    config.schedule.store = str(tmp_path / "routines.yaml")
    config.schedule.log = str(tmp_path / "routines.log")
    config.safety.audit_log = str(tmp_path / "audit.log")
    return config


@pytest.fixture()
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.calls = []  # type: ignore[attr-defined]

    @registry.tool("phone_status", "Status")
    def phone_status() -> dict:
        registry.calls.append("phone_status")  # type: ignore[attr-defined]
        return {"battery_percent": 64}

    @registry.tool("lock_phone", "Lock the phone", dangerous=True)
    def lock_phone() -> str:
        registry.calls.append("lock_phone")  # type: ignore[attr-defined]
        return "phone locked"

    @registry.tool("explode", "Fails")
    def explode() -> str:
        raise RuntimeError("cable unplugged")

    return registry


@pytest.fixture()
def store(config) -> RoutineStore:
    return RoutineStore(config.schedule.store)


def make_runner(config, store, registry, clock, agent_factory=None) -> RoutineRunner:
    return RoutineRunner(
        config, store, registry, agent_factory=agent_factory, clock=clock
    )


def add(store, name="night lock", when="11pm", tool="lock_phone", **kwargs) -> Routine:
    return store.add(
        Routine(name=name, schedule=parse_schedule(when), actions=[Action(tool=tool)], **kwargs)
    )


# --- firing ---------------------------------------------------------------

def test_routine_fires_when_due(config, store, registry):
    add(store, tool="phone_status", allow_dangerous=False)
    clock = FakeClock(MONDAY_10PM)
    runner = make_runner(config, store, registry, clock)
    runner.prime()

    assert runner.tick() == []           # 22:00, not yet
    clock.advance(hours=1, minutes=1)    # 23:01
    assert runner.tick() == ["night lock"]
    assert registry.calls == ["phone_status"]


def test_a_routine_does_not_fire_twice_in_one_window(config, store, registry):
    add(store, tool="phone_status")
    clock = FakeClock(MONDAY_10PM)
    runner = make_runner(config, store, registry, clock)
    runner.prime()
    clock.advance(hours=1, minutes=1)
    runner.tick()
    clock.advance(minutes=5)
    assert runner.tick() == []
    assert registry.calls == ["phone_status"]


def test_next_firing_is_rescheduled_for_the_following_day(config, store, registry):
    routine = add(store, tool="phone_status")
    clock = FakeClock(MONDAY_10PM)
    runner = make_runner(config, store, registry, clock)
    runner.prime()
    clock.advance(hours=1, minutes=1)
    runner.tick()
    assert runner.next_due()[1] == datetime(2026, 10, 6, 23, 0)


def test_disabled_routines_never_fire(config, store, registry):
    add(store, tool="phone_status", enabled=False)
    clock = FakeClock(MONDAY_10PM)
    runner = make_runner(config, store, registry, clock)
    runner.prime()
    clock.advance(hours=2)
    assert runner.tick() == []
    assert registry.calls == []


def test_a_one_off_disables_itself_after_running(config, store, registry):
    store.add(
        Routine(
            name="christmas",
            schedule=parse_schedule("on 2026-10-06 at 08:00"),
            actions=[Action(tool="phone_status")],
        )
    )
    clock = FakeClock(MONDAY_10PM)
    runner = make_runner(config, store, registry, clock)
    runner.prime()
    clock.advance(hours=10)
    assert runner.tick() == ["christmas"]
    assert store.get("christmas").enabled is False


# --- missed runs ----------------------------------------------------------

def test_a_long_missed_run_is_skipped_not_fired_late(config, store, registry):
    # The laptop was asleep at 23:00; locking the phone at 07:00 is wrong.
    add(store, tool="phone_status")
    clock = FakeClock(MONDAY_10PM)
    runner = make_runner(config, store, registry, clock)
    runner.prime()
    clock.advance(hours=9)
    assert runner.tick() == []
    assert registry.calls == []


def test_normal_tick_delay_is_not_treated_as_a_missed_run(config, store, registry):
    # The runner wakes every tick_seconds, so a firing is always a little
    # late. That must not count as missed, or nothing would ever run.
    add(store, tool="phone_status")
    clock = FakeClock(MONDAY_10PM)
    runner = make_runner(config, store, registry, clock)
    runner.prime()
    clock.advance(hours=1, minutes=3)
    assert runner.tick() == ["night lock"]


def test_catch_up_window_allows_a_slightly_late_run(config, store, registry):
    config.schedule.catch_up_minutes = 120
    add(store, tool="phone_status")
    clock = FakeClock(MONDAY_10PM)
    runner = make_runner(config, store, registry, clock)
    runner.prime()
    clock.advance(hours=1, minutes=30)
    assert runner.tick() == ["night lock"]


# --- unattended safety ----------------------------------------------------

def test_dangerous_tool_is_skipped_without_permission(config, store, registry):
    routine = add(store, tool="lock_phone", allow_dangerous=False)
    runner = make_runner(config, store, registry, FakeClock(MONDAY_10PM))
    summary = runner.run_now(routine)
    assert "needs allow_dangerous" in summary
    assert registry.calls == []


def test_dangerous_tool_runs_when_permitted_at_creation(config, store, registry):
    routine = add(store, tool="lock_phone", allow_dangerous=True)
    runner = make_runner(config, store, registry, FakeClock(MONDAY_10PM))
    assert "phone locked" in runner.run_now(routine)
    assert registry.calls == ["lock_phone"]


def test_blocked_tools_are_skipped_even_when_permitted(config, store, registry):
    config.safety.blocked_tools = ["lock_phone"]
    routine = add(store, tool="lock_phone", allow_dangerous=True)
    runner = make_runner(config, store, registry, FakeClock(MONDAY_10PM))
    assert "blocked by safety.blocked_tools" in runner.run_now(routine)
    assert registry.calls == []


def test_a_failing_action_does_not_stop_the_routine(config, store, registry):
    routine = store.add(
        Routine(
            name="mixed",
            schedule=parse_schedule("11pm"),
            actions=[Action(tool="explode"), Action(tool="phone_status")],
        )
    )
    runner = make_runner(config, store, registry, FakeClock(MONDAY_10PM))
    summary = runner.run_now(routine)
    assert "cable unplugged" in summary
    assert registry.calls == ["phone_status"]


def test_runs_are_recorded_and_logged(config, store, registry, tmp_path):
    routine = add(store, tool="phone_status")
    runner = make_runner(config, store, registry, FakeClock(MONDAY_10PM))
    runner.run_now(routine)
    assert store.get("night lock").last_run.startswith("2026-10-05")
    entry = json.loads((tmp_path / "routines.log").read_text().splitlines()[-1])
    assert entry["routine"] == "night lock"


# --- prompt actions -------------------------------------------------------

class FakeAgent:
    def __init__(self, turn):
        self.turn = turn
        self.asked = []

    def ask(self, text):
        self.asked.append(text)
        return self.turn


def test_prompt_action_goes_through_the_agent(config, store, registry):
    from nic.agent import Turn

    agent = FakeAgent(Turn(reply="Battery is 64 percent.", steps=[]))
    routine = store.add(
        Routine(
            name="briefing",
            schedule=parse_schedule("07:30"),
            actions=[Action(prompt="how is my phone battery")],
        )
    )
    runner = make_runner(config, store, registry, FakeClock(MONDAY_10PM), lambda: agent)
    assert "Battery is 64 percent." in runner.run_now(routine)
    assert agent.asked == ["how is my phone battery"]


def test_prompt_action_needing_confirmation_is_skipped(config, store, registry):
    from nic.agent import Turn
    from nic.tools.registry import ConfirmationRequired

    pending = ConfirmationRequired("lock_phone", {}, "lock_phone()")
    agent = FakeAgent(Turn(reply="needs approval", steps=[], pending=pending))
    routine = store.add(
        Routine(
            name="ask to lock",
            schedule=parse_schedule("23:00"),
            actions=[Action(prompt="lock my phone")],
        )
    )
    runner = make_runner(config, store, registry, FakeClock(MONDAY_10PM), lambda: agent)
    assert "nobody is here" in runner.run_now(routine)


# --- the agent-facing tools ----------------------------------------------

@pytest.fixture()
def routine_tools(config, store, registry) -> ToolRegistry:
    return build_routine_tools(config, store, registry)


def test_agent_can_create_a_safe_routine(routine_tools, store):
    result = routine_tools.get("routine_add").call(
        {"name": "morning check", "when": "weekdays at 07:30", "tool": "phone_status"}
    )
    assert "saved 'morning check'" in result
    assert store.get("morning check").schedule.describe() == "weekdays at 07:30"


def test_agent_cannot_schedule_a_dangerous_tool_silently(routine_tools, store):
    from nic.tools.registry import ToolError

    with pytest.raises(ToolError, match="allow_dangerous"):
        routine_tools.get("routine_add").call(
            {"name": "night lock", "when": "11pm", "tool": "lock_phone"}
        )
    assert store.all() == []


def test_agent_can_schedule_a_dangerous_tool_once_told_to(routine_tools, store):
    routine_tools.get("routine_add").call(
        {
            "name": "night lock",
            "when": "11pm",
            "tool": "lock_phone",
            "allow_dangerous": True,
        }
    )
    assert store.get("night lock").allow_dangerous is True


def test_agent_gets_a_clear_error_for_an_unknown_tool(routine_tools):
    from nic.tools.registry import ToolError

    with pytest.raises(ToolError, match="unknown tool"):
        routine_tools.get("routine_add").call(
            {"name": "x", "when": "11pm", "tool": "teleport"}
        )


def test_agent_gets_a_clear_error_for_a_bad_schedule(routine_tools):
    from nic.tools.registry import ToolError

    with pytest.raises(ToolError, match="could not"):
        routine_tools.get("routine_add").call(
            {"name": "x", "when": "sometime soon", "tool": "phone_status"}
        )


def test_agent_can_list_disable_and_remove(routine_tools, store):
    routine_tools.get("routine_add").call(
        {"name": "morning check", "when": "07:30", "tool": "phone_status"}
    )
    assert any("morning check" in line for line in routine_tools.get("routine_list").call({}))
    routine_tools.get("routine_enable").call({"name": "morning check", "enabled": False})
    assert store.get("morning check").enabled is False
    routine_tools.get("routine_remove").call({"name": "morning check"})
    assert store.all() == []


def test_routine_tools_are_marked_for_confirmation(routine_tools):
    assert routine_tools.get("routine_add").dangerous is True
    assert routine_tools.get("routine_remove").dangerous is True
    assert routine_tools.get("routine_list").dangerous is False
