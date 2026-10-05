# Offline voice

Speech in uses **faster-whisper** on CPU; speech out uses **Piper**, with
the Windows built-in voice as a fallback. Neither sends audio anywhere.

## Install

```powershell
pip install -r requirements-voice.txt
```

## Speech out: Piper (optional but much nicer)

1. Download `piper_windows_amd64.zip` from
   <https://github.com/rhasspy/piper/releases> and unzip it.
2. Download a voice, e.g. `en_US-amy-medium.onnx` plus its `.json` from
   <https://huggingface.co/rhasspy/piper-voices>.
3. Point `config.yaml` at both:

```yaml
voice:
  enabled: true
  stt_model: base.en      # tiny.en if your laptop struggles
  piper_path: C:\piper\piper.exe
  piper_voice: C:\piper\voices\en_US-amy-medium.onnx
```

If you skip Piper, Windows' built-in SAPI voice is used automatically -
robotic, but zero setup and fully offline.

## Use

```powershell
python -m nic listen          # press Enter, speak, it acts and replies aloud
python -m nic chat --speak    # type, hear the reply
```

## Performance on a CPU-only laptop

| Whisper model | RAM | ~6s clip |
| --- | --- | --- |
| `tiny.en` | ~0.5 GB | under 1s |
| `base.en` | ~1 GB | 1-2s |
| `small.en` | ~2 GB | 3-5s |

The model load happens once, on first use, so the first request is slower
than the rest. Running Whisper and an 8B LLM at once on 8 GB is tight -
pair `tiny.en` with `qwen3:4b` on that hardware.
