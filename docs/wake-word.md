# Always-listening mode

```powershell
python -m nic enroll     # once: teach it your voice
python -m nic wake       # then: hands-free
```

```
Listening for 'nic'. Owner voice only.
(noise floor 0.0031)
(ignored: different voice (similarity 0.71 < 0.86))
you> what is my phone battery
  . android_status -> {"battery_percent": 64, ...}
nic> Sixty four percent, not charging.
```

## How a sound becomes an action

```
microphone -> VAD segment -> speaker check -> transcribe -> wake word? -> agent -> speech
```

The speaker check sits **before** transcription on purpose: audio from
anyone who is not you is discarded without being turned into text and
without waking the model. That saves CPU and means other people's
conversations are never processed.

## Talking to it

- **"Nic, lock my phone"** - wake word plus command in one breath.
- **"Nic?"** - it answers "Yes?" and listens for your command.
- **Follow-ups** - for `voice.follow_up_seconds` (25s default) after a
  reply, you can keep talking without saying the name again.
- **"Stop listening"** - closes that window; the wake word is needed again.
- **Dangerous actions** - it says *"laptop_power(action='shutdown').
  Say yes to allow it"* and waits. Anything that is not a yes cancels.

The name must start the sentence (after an optional "hey" or "okay"), so
"I told Nick about it yesterday" does not trigger it. Whisper's spelling
of a short name is unreliable, so `wake_variants` accepts nick/nik/nix and
anything within one typo.

## Tuning

| Symptom | Setting | Direction |
| --- | --- | --- |
| Misses quiet speech | `voice.vad_floor_minimum` | lower (0.006) |
| Triggers on room noise | `voice.vad_floor_minimum` | raise (0.02) |
| Cuts you off mid-sentence | `voice.vad_silence_seconds` | raise (1.2) |
| Slow to respond | `voice.vad_silence_seconds` | lower (0.5) |
| Replies to the TV | enroll your voice, raise `speaker_threshold` | |

The loop measures your room's noise floor at startup, so start it when
the room sounds normal, not mid-sneeze.

## A lighter wake word (optional)

The default engine transcribes every utterance to look for the name,
which costs CPU on a machine already running a model. If that is tight:

```powershell
pip install openwakeword
```

```yaml
voice:
  wake_engine: openwakeword
  openwakeword_model: C:\models\hey_jarvis_v0.1.onnx
```

openWakeWord scores raw frames continuously and is far cheaper, at the
cost of only recognising the wake words it has models for.

## Running it at login

Save as `nic-wake.bat` beside `run-nic.bat` and put a shortcut to it in
`shell:startup`:

```bat
@echo off
cd /d "%~dp0"
start "" /min ollama serve
python -m nic wake
```
