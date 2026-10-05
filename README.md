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
| Wake word | VAD + transcript match, or openWakeWord | hands-free, no cloud |
| Speaker ID | MFCC embedder, or Resemblyzer if installed | acts only on your voice |
| Interfaces | terminal, voice, installable phone app | pick per moment |

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
python -m nic enroll          # record your voiceprint, once
python -m nic wake            # always listening: wake word + your voice only
python -m nic listen          # push-to-talk voice conversation
python -m nic serve           # web UI on your Wi-Fi, for the phone
python -m nic qr              # address + QR code to scan from the phone
python -m nic tools           # list every capability
python -m nic doctor          # diagnose the setup
```

`run-nic.bat` starts Ollama and the web UI in one double-click.

## Hands-free

```powershell
python -m nic enroll     # once
python -m nic wake
```

```
Listening for 'nic'. Owner voice only.
(ignored: different voice (similarity 0.71 < 0.86))
you> what is my phone battery
nic> Sixty four percent, not charging.
```

Say **"Nic, lock my phone"**, or just **"Nic?"** and wait for "Yes?".
After a reply it keeps listening for 25 seconds, so follow-ups need no
wake word; **"stop listening"** closes that window. Dangerous actions are
read aloud and wait for a spoken yes.

Each sound goes: mic -> voice-activity segment -> **speaker check** ->
transcribe -> wake word -> agent. The speaker check comes first, so other
people's speech is dropped before it is ever transcribed.

Details: [docs/wake-word.md](docs/wake-word.md).

## Only listening to you

`python -m nic enroll` records five short clips and saves a voiceprint.
Everything that does not match it is ignored. Two embedders are
supported: a pure-numpy MFCC one that needs nothing extra (the default -
torch is a ~2 GB install on a laptop this size), and Resemblyzer if you
install it, which is more accurate.

**It is a filter, not a lock.** It stops the assistant reacting to the TV,
your family, or a podcast. A recording of your voice will pass it. Keep
confirmations on and do not treat voice as permission -
[docs/voice-id.md](docs/voice-id.md) is honest about the limits.

## On your phone's home screen

`python -m nic serve` prints a LAN address like
`http://192.168.1.20:8713` (`python -m nic qr` shows it as a QR code).
Open it on the phone, paste the access token once, and install it:

- **Android (Chrome):** tap **Add** on the prompt nic shows, or
  **⋮ -> Add to Home screen**.
- **iPhone (Safari):** **Share -> Add to Home Screen**.

You get a `nic` icon on the home tab that opens full-screen with no
address bar. The **Talk** button uses the *phone's* speech recognition
(not offline on most Androids); the fully offline voice path is
`nic wake` on the laptop. Dangerous actions show an Allow/Cancel panel.

Details: [docs/phone-shortcut.md](docs/phone-shortcut.md).

## Safety

- Dangerous tools (power, lock, calls, shell) ask before running - by
  prompt, by Allow/Cancel panel, or by spoken yes in wake mode.
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
  audio.py        mic capture and voice-activity segmentation
  voice.py        faster-whisper in, Piper out
  voiceid.py      voiceprints: MFCC and Resemblyzer embedders
  enrollment.py   interactive 'nic enroll'
  wake.py         always-listening loop and wake-word matching
  cli.py          chat / ask / enroll / wake / listen / serve / qr / tools / doctor
  tools/
    registry.py   tool decorator and JSON schemas
    laptop.py     Windows control
    android.py    ADB control
    ios.py        Shortcuts bridge
  web/            stdlib HTTP server + installable phone app (PWA)
docs/             setup, android, ios, voice, wake word, voice id, phone, safety
tests/            96 tests, no device, mic or model required
```

## Tests

```powershell
pip install pytest
python -m pytest -q
```

The suite stubs the model, the devices, and the microphone (synthetic
voices stand in for speakers), so it runs anywhere - no phone, no mic, no
Ollama, no Windows needed.

## Hardware expectations

On 8-16 GB with no GPU, `qwen3:4b` answers in a few seconds and tool calls
land reliably. `qwen3:8b` or `llama3.1:8b` reason better and run roughly
twice as slow; set `model.name` in `config.yaml` to switch. Nothing else
needs to change.
