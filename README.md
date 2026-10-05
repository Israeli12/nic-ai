# nic-ai

A private assistant that runs **entirely offline** on your Windows laptop
and controls your laptop and your phone. No API keys, no accounts, no
network calls once it is installed.

```
you> how much battery has my phone got, and lock it
  . android_status -> {"battery_percent": 64, "charging": false, ...}
  [confirm] android_lock()
  allow this? [y/N] y
  . android_lock -> phone locked
nic> 64 percent, not charging. Phone locked.
```

## What it is

| Piece | Choice | Why |
| --- | --- | --- |
| Brain | [Ollama](https://ollama.com) running `qwen3:4b` locally | ~3 GB, good tool calling, usable on CPU |
| Laptop control | PowerShell + Win32 calls | apps, volume, media, screenshots, clipboard, files, lock, power |
| Phone control (Android) | ADB over USB or your own Wi-Fi | apps, keys, taps, typing, screenshots, notifications, SMS, calls, files |
| Phone control (iPhone) | Shortcuts bridge over LAN | the ceiling iOS allows - see below |
| Voice | faster-whisper (in) + Piper (out) | both CPU, both offline |
| Interfaces | terminal, voice, phone-friendly web UI | pick per moment |

Dependencies are deliberately thin: PyYAML and Pillow for the core, and
the web UI is standard-library only.

## Honest note about iPhone

Android can be controlled properly. **iPhone cannot** - Apple exposes no
offline device-control API, so the iOS side can only run Shortcuts you
built yourself, triggered over your Wi-Fi. No taps, no screenshots, no
reading app state. The Android handset is where real control lives;
[docs/ios.md](docs/ios.md) explains the bridge and its limits.

## Quick start

```powershell
git clone <this repo> && cd nic-ai
python -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# install Ollama from https://ollama.com/download, then:
ollama pull qwen3:4b

copy config.example.yaml config.yaml
python -c "import secrets; print(secrets.token_urlsafe(24))"   # paste into web.access_token

python -m nic doctor     # checks model, adb, voice, web end to end
python -m nic chat
```

Full walkthrough: [docs/setup-windows.md](docs/setup-windows.md).

## Commands

```powershell
python -m nic chat            # interactive text chat
python -m nic chat --speak    # ...with spoken replies
python -m nic ask "lock my phone"
python -m nic listen          # voice conversation
python -m nic serve           # web UI on your Wi-Fi, for the phone
python -m nic tools           # list every capability
python -m nic doctor          # diagnose the setup
```

`run-nic.bat` starts Ollama and the web UI in one double-click.

## Using it from your phone

`python -m nic serve` prints a LAN address like
`http://192.168.1.20:8713`. Open that on your phone, paste the access
token once, and you have a chat UI that drives your laptop - including
"Add to Home Screen" for an app-like icon. Dangerous actions show an
Allow/Cancel panel instead of running.

## Safety

- Dangerous tools (power, lock, calls, shell) ask before running.
- `safety.blocked_tools` removes capabilities entirely.
- Shell access is off unless you set `laptop.allow_shell: true`.
- Only allowlisted apps can be launched.
- Every tool call is appended to `~/nic-ai/audit.log`.
- The web UI requires a token and refuses to start without one.

Details and the reasoning: [docs/safety.md](docs/safety.md).

## Adding your own capability

Tools are plain functions with a JSON schema. Add one to
`nic/tools/laptop.py` (or a new module) and it is immediately available to
the model:

```python
@registry.tool(
    "laptop_wifi_networks",
    "List Wi-Fi networks the laptop can see.",
)
def laptop_wifi_networks() -> str:
    return run_powershell("netsh wlan show networks")
```

Mark anything irreversible with `dangerous=True` and it inherits the
confirmation flow.

## Layout

```
nic/
  agent.py        model loop: propose tool -> run -> feed result back
  llm.py          Ollama client over loopback
  config.py       config.yaml + NIC_* environment overrides
  safety.py       confirmations, blocklist, audit log
  voice.py        faster-whisper in, Piper out
  cli.py          chat / ask / listen / serve / tools / doctor
  tools/
    registry.py   tool decorator and JSON schemas
    laptop.py     Windows control
    android.py    ADB control
    ios.py        Shortcuts bridge
  web/            stdlib HTTP server + phone-friendly UI
docs/             setup, android, ios, voice, safety
tests/            45 tests, no device or model required
```

## Tests

```powershell
pip install pytest
python -m pytest -q
```

The suite stubs the model and the devices, so it runs anywhere - no phone,
no Ollama, no Windows needed.

## Hardware expectations

On 8-16 GB with no GPU, `qwen3:4b` answers in a few seconds and tool calls
land reliably. `qwen3:8b` or `llama3.1:8b` reason better and run roughly
twice as slow; set `model.name` in `config.yaml` to switch. Nothing else
needs to change.
