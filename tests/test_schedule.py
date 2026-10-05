from datetime import date, datetime

import pytest

from nic.schedule import (
    Action,
    Routine,
    RoutineStore,
    Schedule,
    ScheduleError,
    parse_schedule,
    parse_time,
    summarise,
)

MONDAY_10PM = datetime(2026, 10, 5, 22, 0)  # a Monday


# --- time parsing ---------------------------------------------------------

@pytest.mark.parametrize(
    "text,minutes",
    [
        ("23:00", 23 * 60),
        ("11pm", 23 * 60),
        ("11 pm", 23 * 60),
        ("7:30am", 7 * 60 + 30),
        ("7.30am", 7 * 60 + 30),
        ("12am", 0),
        ("12pm", 12 * 60),
        ("00:05", 5),
    ],
)
def test_times_people_actually_type(text, minutes):
    assert parse_time(text) == minutes


@pytest.mark.parametrize("text", ["25:00", "11:70", "half past six", "", "13pm"])
def test_nonsense_times_are_rejected(text):
    with pytest.raises(ScheduleError):
        parse_time(text)


# --- schedule parsing -----------------------------------------------------

def test_bare_time_means_every_day():
    schedule = parse_schedule("11pm")
    assert schedule.kind == "daily"
    assert schedule.next_after(MONDAY_10PM) == datetime(2026, 10, 5, 23, 0)


def test_daily_rolls_to_tomorrow_once_passed():
    schedule = parse_schedule("every day at 21:00")
    assert schedule.next_after(MONDAY_10PM) == datetime(2026, 10, 6, 21, 0)


def test_weekdays_skips_the_weekend():
    schedule = parse_schedule("weekdays at 07:30")
    friday_evening = datetime(2026, 10, 9, 20, 0)
    assert schedule.next_after(friday_evening) == datetime(2026, 10, 12, 7, 30)


def test_weekends_only_fires_at_the_weekend():
    schedule = parse_schedule("weekends at 09:00")
    assert schedule.next_after(MONDAY_10PM) == datetime(2026, 10, 10, 9, 0)


def test_named_days():
    schedule = parse_schedule("mon,fri at 18:00")
    assert schedule.weekdays == frozenset({0, 4})
    assert schedule.next_after(MONDAY_10PM) == datetime(2026, 10, 9, 18, 0)


def test_days_joined_with_and():
    assert parse_schedule("tue and thu at 08:00").weekdays == frozenset({1, 3})


def test_interval_in_minutes_and_hours():
    assert parse_schedule("every 30 minutes").interval_minutes == 30
    assert parse_schedule("every 2 hours").interval_minutes == 120
    assert parse_schedule("every 45 min").next_after(MONDAY_10PM) == datetime(2026, 10, 5, 22, 45)


def test_one_off_runs_once_then_never():
    schedule = parse_schedule("on 2026-12-25 at 08:00")
    assert schedule.on_date == date(2026, 12, 25)
    assert schedule.next_after(MONDAY_10PM) == datetime(2026, 12, 25, 8, 0)
    assert schedule.next_after(datetime(2026, 12, 26, 0, 0)) is None


@pytest.mark.parametrize(
    "text",
    ["", "sometime", "every day at teatime", "blursday at 09:00", "every 0 minutes"],
)
def test_unparseable_schedules_explain_themselves(text):
    with pytest.raises(ScheduleError) as excinfo:
        parse_schedule(text)
    # Every rejection must suggest what would have worked.
    message = str(excinfo.value)
    assert any(hint in message for hint in ("every day at", "at least 1", "try '23:00'"))


def test_describe_is_readable():
    assert parse_schedule("11pm").describe() == "every day at 23:00"
    assert parse_schedule("weekdays at 7:30am").describe() == "weekdays at 07:30"
    assert parse_schedule("every 2 hours").describe() == "every 2 hours"


# --- store ----------------------------------------------------------------

def routine(name="night lock", when="11pm", tool="lock", **kwargs) -> Routine:
    return Routine(
        name=name,
        schedule=parse_schedule(when),
        actions=[Action(tool=tool)],
        **kwargs,
    )


@pytest.fixture()
def store(tmp_path) -> RoutineStore:
    return RoutineStore(tmp_path / "routines.yaml")


def test_empty_store_has_no_routines(store):
    assert store.all() == []


def test_routines_survive_a_round_trip(store, tmp_path):
    store.add(routine(allow_dangerous=True))
    reloaded = RoutineStore(tmp_path / "routines.yaml")
    saved = reloaded.get("night lock")
    assert saved.schedule.describe() == "every day at 23:00"
    assert saved.actions[0].tool == "lock"
    assert saved.allow_dangerous is True


def test_duplicate_names_are_refused(store):
    store.add(routine())
    with pytest.raises(ScheduleError, match="already exists"):
        store.add(routine())


def test_replace_overwrites_in_place(store):
    store.add(routine())
    store.add(routine(when="07:00"), replace=True)
    assert store.get("night lock").schedule.minute_of_day == 7 * 60
    assert len(store.all()) == 1


def test_remove_and_missing_name(store):
    store.add(routine())
    assert store.remove("night lock").name == "night lock"
    with pytest.raises(ScheduleError, match="no routine called"):
        store.remove("night lock")


def test_disabled_routine_has_no_next_run(store):
    saved = store.add(routine())
    store.set_enabled("night lock", False)
    assert saved.next_run(MONDAY_10PM) is None


def test_routine_without_actions_is_rejected(tmp_path):
    path = tmp_path / "routines.yaml"
    path.write_text("routines:\n- name: broken\n  when: 11pm\n", encoding="utf-8")
    with pytest.raises(ScheduleError, match="no actions"):
        RoutineStore(path)


def test_summarise_includes_the_next_run(store):
    store.add(routine())
    line = summarise(store.all(), MONDAY_10PM)[0]
    assert "night lock" in line and "next: Mon 05 Oct 23:00" in line
