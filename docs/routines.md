# Scheduled routines

A routine is a schedule plus one or more actions. Actions are tool calls,
so a routine does not need the model to run - "lock the phone at 11pm"
fires the tool directly, which is both faster and predictable.

## Creating one

By voice or chat, which is usually easiest:

> **"Nic, lock my phone every night at 11."**

The assistant calls `routine_add`. Because locking is a confirm-required
tool, it will ask whether the routine may do that unattended before
saving it.

Or from the terminal:

```powershell
python -m nic schedule add "night lock" --when "11pm" --tool android_lock
python -m nic schedule add "morning check" --when "weekdays at 07:30" --tool android_status
python -m nic schedule add "hourly battery" --when "every 2 hours" --tool laptop_status
```

```
'android_lock' normally asks for confirmation before running.
This routine would run it unattended: every day at 23:00.
allow that? [y/N] y
saved: night lock: every day at 23:00 -> android_lock()
next run: Mon 05 Oct 23:00
```

## Schedules it understands

| You write | It means |
| --- | --- |
| `11pm`, `23:00`, `7:30am` | every day at that time |
| `every day at 23:00` | the same |
| `weekdays at 07:30` | Mon-Fri |
| `weekends at 09:00` | Sat-Sun |
| `mon,fri at 18:00` | those days |
| `tue and thu at 08:00` | those days |
| `every 30 minutes`, `every 2 hours` | an interval from when it was saved |
| `on 2026-12-25 at 08:00` | once, then it disables itself |

Anything it cannot parse is rejected with examples rather than guessed at.

## Managing them

```powershell
python -m nic schedule list              # with each one's next run time
python -m nic schedule run "night lock"  # fire it now, to test it
python -m nic schedule disable "night lock"
python -m nic schedule enable "night lock"
python -m nic schedule remove "night lock"
```

Routines live in `~/nic-ai/routines.yaml`, which is plain editable YAML:

```yaml
routines:
- name: night lock
  when: 11pm
  actions:
  - tool: android_lock
  enabled: true
  allow_dangerous: true
```

Each run is appended to `~/nic-ai/routines.log`, and the routine records
its own `last_run` and `last_result`.

## Running the scheduler

It runs automatically inside the two long-running commands:

```powershell
python -m nic serve        # web UI + routines
python -m nic wake         # voice + routines
python -m nic schedule serve   # routines only
```

Turn either off with `schedule.run_with_serve` / `schedule.run_with_wake`.
For routines to fire at 11pm, something must be running at 11pm - put a
shortcut to `run-nic.bat` in `shell:startup` (see
[setup-windows.md](setup-windows.md)) and leave the laptop on.

## Unattended means nobody can say yes

This is the part worth understanding. Dangerous tools normally stop and
ask. At 11pm there is nobody to ask, so the decision moves to creation
time: a routine containing a confirm-required tool must carry
`allow_dangerous: true`, and both the CLI and the assistant have to ask
you for that explicitly before saving. Without it, the routine still
runs, and that action is skipped and logged:

```
[routine] night lock: android_lock() -> skipped: needs allow_dangerous,
          since nobody is present to confirm it
```

`safety.blocked_tools` outranks everything: a blocked tool is skipped in
routines too, whatever the routine says.

## Missed runs

If the laptop is asleep at 23:00, the slot is **skipped**, not fired late
when it wakes - locking your phone at 07:00 because 23:00 was missed is
worse than not locking it. A few minutes of lateness is normal (the
scheduler ticks every `tick_seconds`) and still counts as on time.

To honour a missed slot anyway, allow extra lateness:

```yaml
schedule:
  catch_up_minutes: 120   # fire up to 2 hours late after waking
```

## Prompt actions

A routine can carry a prompt instead of a tool, handed to the model when
it fires. Useful for anything that needs judgement:

```yaml
- name: evening summary
  when: 22:00
  actions:
  - prompt: summarise my phone battery and any notifications
  speak: true
```

This is slower (it wakes the model) and less predictable than a tool
call, so prefer tools for anything mechanical. If the model picks a
confirm-required tool mid-prompt, that step is skipped and logged - a
prompt action cannot be used to sidestep `allow_dangerous`.
