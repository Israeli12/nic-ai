# Only listening to one person

`python -m nic enroll` records a few clips of you speaking and saves a
*voiceprint* to `~/nic-ai/voiceprint.npz`. After that, every utterance is
scored against it, and anything that does not match is dropped before it
is transcribed.

```powershell
python -m nic enroll --samples 5
```

```
Stay quiet for a moment while I measure the room...
noise floor 0.0029

[1/5] Say: "Nic, what is my phone battery at?"
    captured 2.1s
...
saved C:\Users\you\nic-ai\voiceprint.npz
embedder   mfcc
threshold  0.86
clip match 0.912 worst, 0.981 best
```

Enroll in the room and at the distance you will actually use, with the
microphone you will actually use. A voiceprint from a headset will not
match you across the room.

## Be clear about what this is

It is a **filter, not a lock**. It stops the assistant reacting to the
TV, your family, a podcast, or a colleague walking past. It does not stop
a determined person: a recording of your voice will pass, and a close
family member sometimes will too. Nothing about it is cryptographic.

So: keep `safety.confirm_dangerous_actions: true`, and do not treat voice
as permission to do something you would not let a person in the room do.

## Two embedders

| Engine | Install | Accuracy | Notes |
| --- | --- | --- | --- |
| `mfcc` (built in) | nothing | fair | pure numpy, no torch, instant |
| `resemblyzer` | `pip install resemblyzer` | good | trained d-vectors, pulls in torch (~2 GB) |

`voice_id_engine: auto` uses Resemblyzer when it is installed and the
MFCC one otherwise. On a 16 GB CPU-only laptop the MFCC embedder is the
sensible default; switch if it rejects you too often. The voiceprint
records which embedder made it and refuses to be read by the other, so
re-run `enroll` after installing Resemblyzer.

## When it rejects you

The loop prints why:

```
(ignored: different voice (similarity 0.78 < 0.86))
```

If that is you being turned away:

1. Re-enroll in the room you actually use (most common fix).
2. Lower the bar a little: `voice.speaker_threshold: 0.82`.
3. Turn on `voice.adapt_voiceprint: true` so accepted clips slowly pull
   the centroid toward your current voice, microphone, and cold.

If someone else gets through, raise `speaker_threshold` toward 0.92, and
install Resemblyzer - the MFCC embedder is the weaker of the two.

## Turning it off

```yaml
voice:
  require_enrolled_voice: false   # respond to any voice
```

Or for one session: `python -m nic wake --any-voice`.

`python -m nic doctor` reports which voiceprint is enrolled, with which
embedder and threshold.
