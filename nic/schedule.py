"""Scheduled routines: "lock the phone at 11pm", every night.

A routine is a schedule plus one or more actions. Actions are tool calls,
so a routine runs deterministically without waking the model; a routine
may instead carry a prompt, which is handed to the agent.

Nobody is present when a routine fires, so the confirmation flow cannot
apply. Instead the decision is made once, when the routine is created:
a routine containing a dangerous tool must carry `allow_dangerous: true`,
and the CLI and the agent both have to ask for that explicitly.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import Config, expand
from .tools.registry import ToolError, ToolRegistry

try:  # pragma: no cover - optional at import time
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

WEEKDAY_NAMES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
WEEKDAYS = {0, 1, 2, 3, 4}
WEEKENDS = {5, 6}
DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# A firing this late still counts as on time: ticks are not instant.
GRACE_SECONDS = 300


class ScheduleError(ValueError):
    """The schedule text could not be understood."""


def parse_time(text: str) -> int:
    """Parse '11pm', '23:00', '7.30am' into minutes since midnight."""
    cleaned = text.strip().lower().replace(".", ":")
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", cleaned)
    if not match:
        raise ScheduleError(
            f"could not read a time from {text!r}; try '23:00', '11pm' or '7:30am'"
        )
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            raise ScheduleError(f"{text!r} is not a valid 12-hour time")
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError(f"{text!r} is not a valid time")
    if hour == 24:  # pragma: no cover - guarded above, kept for clarity
        hour = 0
    return hour * 60 + minute


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass(frozen=True)
class Schedule:
    """When a routine fires. `text` is what the user actually wrote."""

    kind: str  # daily | weekly | interval | once
    text: str
    minute_of_day: int = 0
    weekdays: frozenset[int] = frozenset()
    interval_minutes: int = 0
    on_date: date | None = None

    def describe(self) -> str:
        if self.kind == "interval":
            if self.interval_minutes % 60 == 0 and self.interval_minutes >= 60:
                hours = self.interval_minutes // 60
                return f"every {hours} hour{'s' if hours > 1 else ''}"
            return f"every {self.interval_minutes} minutes"
        clock = format_time(self.minute_of_day)
        if self.kind == "daily":
            return f"every day at {clock}"
        if self.kind == "once":
            return f"once on {self.on_date:%Y-%m-%d} at {clock}"
        if self.weekdays == WEEKDAYS:
            return f"weekdays at {clock}"
        if self.weekdays == WEEKENDS:
            return f"weekends at {clock}"
        days = ", ".join(DAY_ORDER[day] for day in sorted(self.weekdays))
        return f"{days} at {clock}"

    def next_after(self, moment: datetime) -> datetime | None:
        """First firing strictly after `moment`, or None if it is finished."""
        if self.kind == "interval":
            return moment + timedelta(minutes=self.interval_minutes)

        if self.kind == "once":
            fire = datetime.combine(self.on_date, datetime.min.time()) + timedelta(
                minutes=self.minute_of_day
            )
            return fire if fire > moment else None

        days = self.weekdays if self.kind == "weekly" else set(range(7))
        for offset in range(0, 8):
            day = (moment + timedelta(days=offset)).date()
            if day.weekday() not in days:
                continue
            fire = datetime.combine(day, datetime.min.time()) + timedelta(
                minutes=self.minute_of_day
            )
            if fire > moment:
                return fire
        return None  # pragma: no cover - a week always contains a match


def parse_schedule(text: str) -> Schedule:
    """Understand the small set of phrasings people actually use."""
    raw = " ".join(text.strip().lower().split())
    if not raw:
        raise ScheduleError("a schedule is required, e.g. 'every day at 23:00'")
    # "at 11pm every day" reads the same as "every day at 11pm".
    body = re.sub(r"^(?:every\s+)?day at\b", "daily at", raw)

    match = re.fullmatch(r"every (\d+) (minute|minutes|min|hour|hours|hr|hrs)", body)
    if match:
        count = int(match.group(1))
        if count < 1:
            raise ScheduleError("an interval must be at least 1 minute")
        minutes = count * (60 if match.group(2).startswith(("hour", "hr")) else 1)
        return Schedule(kind="interval", text=text.strip(), interval_minutes=minutes)

    match = re.fullmatch(r"(?:once |one off )?on (\d{4}-\d{2}-\d{2}) at (.+)", body)
    if match:
        return Schedule(
            kind="once",
            text=text.strip(),
            minute_of_day=parse_time(match.group(2)),
            on_date=date.fromisoformat(match.group(1)),
        )

    match = re.fullmatch(r"(.+?) at (.+)", body)
    if match:
        when, clock = match.group(1).strip(), match.group(2)
        minute_of_day = parse_time(clock)
        if when in {"daily", "every day", "each day", "day"}:
            return Schedule(kind="daily", text=text.strip(), minute_of_day=minute_of_day)
        if when in {"weekdays", "every weekday", "weekday"}:
            return Schedule("weekly", text.strip(), minute_of_day, frozenset(WEEKDAYS))
        if when in {"weekends", "every weekend", "weekend"}:
            return Schedule("weekly", text.strip(), minute_of_day, frozenset(WEEKENDS))
        names = [part.strip() for part in re.split(r"[,/]| and ", when.replace("every ", "")) if part.strip()]
        if names and all(name in WEEKDAY_NAMES for name in names):
            return Schedule(
                "weekly", text.strip(), minute_of_day, frozenset(WEEKDAY_NAMES[n] for n in names)
            )
        raise ScheduleError(
            f"could not read the days in {text!r}."
            " Try 'every day at 23:00', 'weekdays at 07:30', or 'mon,wed at 18:00'"
        )

    # A bare time means every day, which is what people mean by "at 11pm".
    try:
        return Schedule(kind="daily", text=text.strip(), minute_of_day=parse_time(body))
    except ScheduleError:
        raise ScheduleError(
            f"could not understand the schedule {text!r}. Examples:"
            " 'every day at 23:00', 'weekdays at 07:30', 'mon,fri at 18:00',"
            " 'every 30 minutes', 'on 2026-01-01 at 09:00'"
        ) from None


@dataclass
class Action:
    """One step of a routine: a tool call, or a prompt for the agent."""

    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    prompt: str = ""

    def describe(self) -> str:
        if self.prompt:
            return f'ask: "{self.prompt}"'
        if self.arguments:
            rendered = ", ".join(f"{key}={value!r}" for key, value in sorted(self.arguments.items()))
            return f"{self.tool}({rendered})"
        return f"{self.tool}()"

    def to_dict(self) -> dict[str, Any]:
        if self.prompt:
            return {"prompt": self.prompt}
        data: dict[str, Any] = {"tool": self.tool}
        if self.arguments:
            data["arguments"] = self.arguments
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        if "prompt" in data and data["prompt"]:
            return cls(prompt=str(data["prompt"]))
        tool = str(data.get("tool", "")).strip()
        if not tool:
            raise ScheduleError("each action needs a 'tool' or a 'prompt'")
        arguments = data.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ScheduleError(f"arguments for {tool} must be a mapping")
        return cls(tool=tool, arguments=arguments)


@dataclass
class Routine:
    name: str
    schedule: Schedule
    actions: list[Action]
    enabled: bool = True
    # Set once, by the person creating the routine: dangerous tools cannot
    # ask for confirmation at 11pm when nobody is there to answer.
    allow_dangerous: bool = False
    speak: bool = False
    last_run: str = ""
    last_result: str = ""

    def describe(self) -> str:
        state = "" if self.enabled else " (disabled)"
        steps = "; ".join(action.describe() for action in self.actions)
        return f"{self.name}{state}: {self.schedule.describe()} -> {steps}"

    def next_run(self, after: datetime | None = None) -> datetime | None:
        if not self.enabled:
            return None
        return self.schedule.next_after(after or datetime.now())

    def to_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "when": self.schedule.text,
            "actions": [action.to_dict() for action in self.actions],
            "enabled": self.enabled,
        }
        if self.allow_dangerous:
            data["allow_dangerous"] = True
        if self.speak:
            data["speak"] = True
        if self.last_run:
            data["last_run"] = self.last_run
        if self.last_result:
            data["last_result"] = self.last_result
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Routine":
        name = str(data.get("name", "")).strip()
        if not name:
            raise ScheduleError("every routine needs a name")
        raw_actions = data.get("actions") or []
        if not raw_actions:
            raise ScheduleError(f"routine '{name}' has no actions")
        return cls(
            name=name,
            schedule=parse_schedule(str(data.get("when", ""))),
            actions=[Action.from_dict(item) for item in raw_actions],
            enabled=bool(data.get("enabled", True)),
            allow_dangerous=bool(data.get("allow_dangerous", False)),
            speak=bool(data.get("speak", False)),
            last_run=str(data.get("last_run", "")),
            last_result=str(data.get("last_result", "")),
        )


class RoutineStore:
    """Routines on disk, as editable YAML."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._routines: list[Routine] = []
        self.load()

    def load(self) -> list[Routine]:
        if not self.path.exists():
            self._routines = []
            return self._routines
        if yaml is None:  # pragma: no cover - PyYAML is a core dependency
            raise ScheduleError("PyYAML is required to read routines; pip install pyyaml")
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        entries = raw.get("routines", raw if isinstance(raw, list) else [])
        self._routines = [Routine.from_dict(entry) for entry in entries]
        return self._routines

    def save(self) -> Path:
        if yaml is None:  # pragma: no cover
            raise ScheduleError("PyYAML is required to save routines; pip install pyyaml")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"routines": [routine.to_dict() for routine in self._routines]}
        self.path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return self.path

    # -- collection access ------------------------------------------------

    def all(self) -> list[Routine]:
        return list(self._routines)

    def get(self, name: str) -> Routine:
        needle = name.strip().lower()
        for routine in self._routines:
            if routine.name.lower() == needle:
                return routine
        raise ScheduleError(f"no routine called '{name}'")

    def add(self, routine: Routine, replace: bool = False) -> Routine:
        try:
            existing = self.get(routine.name)
        except ScheduleError:
            existing = None
        if existing is not None:
            if not replace:
                raise ScheduleError(
                    f"a routine called '{routine.name}' already exists;"
                    " remove it first or pick another name"
                )
            self._routines.remove(existing)
        self._routines.append(routine)
        self.save()
        return routine

    def remove(self, name: str) -> Routine:
        routine = self.get(name)
        self._routines.remove(routine)
        self.save()
        return routine

    def set_enabled(self, name: str, enabled: bool) -> Routine:
        routine = self.get(name)
        routine.enabled = enabled
        self.save()
        return routine

    def record_run(self, routine: Routine, when: datetime, result: str) -> None:
        routine.last_run = when.strftime("%Y-%m-%d %H:%M:%S")
        routine.last_result = result[:300]
        if routine.schedule.kind == "once":
            # A one-off has nothing left to do.
            routine.enabled = False
        self.save()


class RoutineRunner:
    """Fires routines when they come due.

    `now` is injectable so the whole thing is testable without sleeping.
    """

    def __init__(
        self,
        config: Config,
        store: RoutineStore,
        registry: ToolRegistry,
        agent_factory: Callable[[], Any] | None = None,
        on_event: Callable[[str], None] | None = None,
        speaker=None,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.config = config
        self.store = store
        self.registry = registry
        self.agent_factory = agent_factory
        self.on_event = on_event or (lambda message: None)
        self.speaker = speaker
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._due: dict[str, datetime] = {}
        self._log_path = expand(config.schedule.log)

    # -- scheduling -------------------------------------------------------

    def prime(self) -> None:
        """Work out the next firing for each routine, from now."""
        now = self.clock()
        self._due = {}
        for routine in self.store.all():
            due = routine.next_run(now)
            if due is not None:
                self._due[routine.name] = due

    def next_due(self) -> tuple[str, datetime] | None:
        if not self._due:
            return None
        name = min(self._due, key=lambda key: self._due[key])
        return name, self._due[name]

    def tick(self) -> list[str]:
        """Run everything now due. Returns the names that fired."""
        now = self.clock()
        fired: list[str] = []
        for routine in self.store.all():
            if not routine.enabled:
                self._due.pop(routine.name, None)
                continue
            due = self._due.get(routine.name)
            if due is None:
                due = routine.next_run(now)
                if due is None:
                    continue
                self._due[routine.name] = due
                continue
            if due > now:
                continue
            late_seconds = (now - due).total_seconds()
            if late_seconds > self._tolerated_lateness():
                # The laptop was asleep. Running "lock at 23:00" at 07:00 is
                # worse than skipping it.
                late_minutes = late_seconds / 60
                self._log(routine, due, f"skipped: {late_minutes:.0f} min late")
                self.on_event(f"[routine] {routine.name}: skipped, {late_minutes:.0f} min late")
            else:
                self.run_now(routine)
                fired.append(routine.name)
            following = routine.next_run(now)
            if following is None or not routine.enabled:
                self._due.pop(routine.name, None)
            else:
                self._due[routine.name] = following
        return fired

    def _tolerated_lateness(self) -> float:
        """Seconds a firing may be late before it counts as missed.

        The runner only wakes every `tick_seconds`, and the machine may be
        briefly busy, so a few minutes of lateness is normal operation -
        not a missed run. `catch_up_minutes` extends this for people who do
        want a slot honoured after the laptop wakes up.
        """
        normal_delay = max(GRACE_SECONDS, self.config.schedule.tick_seconds * 3)
        return normal_delay + self.config.schedule.catch_up_minutes * 60

    # -- execution --------------------------------------------------------

    def run_now(self, routine: Routine) -> str:
        """Execute a routine's actions immediately, ignoring its schedule."""
        started = self.clock()
        self.on_event(f"[routine] {routine.name}: running")
        outcomes: list[str] = []
        for action in routine.actions:
            try:
                outcomes.append(self._run_action(routine, action))
            except ToolError as exc:
                outcomes.append(f"{action.describe()} -> error: {exc}")
            except Exception as exc:  # noqa: BLE001 - a routine must not kill the runner
                outcomes.append(f"{action.describe()} -> error: {exc.__class__.__name__}: {exc}")
        summary = " | ".join(outcomes)
        self.store.record_run(routine, started, summary)
        self._log(routine, started, summary)
        self.on_event(f"[routine] {routine.name}: {summary}")
        if routine.speak and self.speaker:
            try:
                self.speaker.say(f"{routine.name}. {outcomes[-1] if outcomes else 'done'}")
            except Exception:  # noqa: BLE001 - audio must never break a routine
                pass
        return summary

    def _run_action(self, routine: Routine, action: Action) -> str:
        if action.prompt:
            if self.agent_factory is None:
                raise ToolError("this runner cannot run prompt actions")
            agent = self.agent_factory()
            turn = agent.ask(action.prompt)
            if turn.pending is not None:
                return (
                    f'ask: "{action.prompt}" -> skipped:'
                    f" {turn.pending.tool_name} needs confirmation and nobody is here"
                )
            return f'ask: "{action.prompt}" -> {turn.reply}'

        tool = self.registry.get(action.tool)
        if action.tool in self.config.safety.blocked_tools:
            return f"{action.describe()} -> skipped: blocked by safety.blocked_tools"
        if tool.dangerous and not routine.allow_dangerous:
            return (
                f"{action.describe()} -> skipped: needs allow_dangerous,"
                " since nobody is present to confirm it"
            )
        result = tool.call(action.arguments)
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str)
        return f"{action.describe()} -> {result}"

    def _log(self, routine: Routine, when: datetime, outcome: str) -> None:
        entry = {
            "at": when.strftime("%Y-%m-%dT%H:%M:%S"),
            "routine": routine.name,
            "outcome": outcome,
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    # -- background thread ------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self.prime()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nic-routines", daemon=True)
        self._thread.start()
        upcoming = self.next_due()
        if upcoming:
            self.on_event(f"[routines] next: {upcoming[0]} at {upcoming[1]:%Y-%m-%d %H:%M}")
        else:
            self.on_event("[routines] none scheduled")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - keep the scheduler alive
                self.on_event(f"[routines] error: {exc}")
            self._stop.wait(self.config.schedule.tick_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


def summarise(routines: Iterable[Routine], now: datetime | None = None) -> list[str]:
    """One readable line per routine, including when it fires next."""
    moment = now or datetime.now()
    lines = []
    for routine in routines:
        due = routine.next_run(moment)
        when = f"{due:%a %d %b %H:%M}" if due else ("disabled" if not routine.enabled else "never")
        lines.append(f"{routine.describe()}  [next: {when}]")
    return lines
