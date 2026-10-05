"""Tools that let the assistant manage its own scheduled routines.

So "Nic, lock my phone every night at 11" creates a routine rather than
needing you to edit YAML.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..config import Config
from ..schedule import (
    Action,
    Routine,
    RoutineStore,
    ScheduleError,
    parse_schedule,
    summarise,
)
from .registry import ToolError, ToolRegistry, object_schema, string_param


def build_registry(config: Config, store: RoutineStore, tools: ToolRegistry) -> ToolRegistry:
    """Routine-management tools. `tools` is the registry routines may call."""
    registry = ToolRegistry()

    @registry.tool(
        "routine_list",
        "List the saved scheduled routines and when each one next runs.",
        surface="schedule",
    )
    def routine_list() -> list[str]:
        store.load()
        return summarise(store.all()) or ["no routines saved"]

    @registry.tool(
        "routine_add",
        "Save a scheduled routine that runs a tool automatically."
        " Schedule examples: 'every day at 23:00', 'weekdays at 07:30',"
        " 'mon,fri at 18:00', 'every 30 minutes', 'on 2026-12-25 at 08:00'."
        " Set allow_dangerous only if the user clearly agreed that this may"
        " run unattended, because nobody can confirm it when it fires.",
        object_schema(
            {
                "name": string_param("Short name for the routine, e.g. 'night lock'"),
                "when": string_param("When it runs, in plain English"),
                "tool": string_param("Name of the tool to run"),
                "arguments": {
                    "type": "object",
                    "description": "Arguments for that tool",
                    "properties": {},
                },
                "allow_dangerous": {
                    "type": "boolean",
                    "description": "Allow a confirm-required tool to run unattended",
                },
            },
            ["name", "when", "tool"],
        ),
        dangerous=True,
        surface="schedule",
    )
    def routine_add(
        name: str,
        when: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        allow_dangerous: bool = False,
    ) -> str:
        store.load()
        try:
            schedule = parse_schedule(when)
        except ScheduleError as exc:
            raise ToolError(str(exc)) from exc
        target = tools.get(tool)  # raises if the tool does not exist
        if target.dangerous and not allow_dangerous:
            raise ToolError(
                f"{tool} asks for confirmation before it runs, and nobody is present"
                " when a routine fires. Ask the user whether this routine may run it"
                " unattended, then set allow_dangerous."
            )
        routine = Routine(
            name=name.strip(),
            schedule=schedule,
            actions=[Action(tool=tool, arguments=arguments or {})],
            allow_dangerous=bool(allow_dangerous),
        )
        try:
            store.add(routine)
        except ScheduleError as exc:
            raise ToolError(str(exc)) from exc
        due = routine.next_run(datetime.now())
        return f"saved '{routine.name}': {routine.describe()}. Next run {due:%a %d %b %H:%M}."

    @registry.tool(
        "routine_remove",
        "Delete a saved routine by name.",
        object_schema({"name": string_param("Routine name")}, ["name"]),
        dangerous=True,
        surface="schedule",
    )
    def routine_remove(name: str) -> str:
        store.load()
        try:
            return f"removed '{store.remove(name).name}'"
        except ScheduleError as exc:
            raise ToolError(str(exc)) from exc

    @registry.tool(
        "routine_enable",
        "Enable or disable a saved routine without deleting it.",
        object_schema(
            {
                "name": string_param("Routine name"),
                "enabled": {"type": "boolean", "description": "true to enable"},
            },
            ["name", "enabled"],
        ),
        surface="schedule",
    )
    def routine_enable(name: str, enabled: bool = True) -> str:
        store.load()
        try:
            routine = store.set_enabled(name, bool(enabled))
        except ScheduleError as exc:
            raise ToolError(str(exc)) from exc
        return f"'{routine.name}' is now {'enabled' if routine.enabled else 'disabled'}"

    return registry
