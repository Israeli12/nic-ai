# Setting up on Windows

Target: a laptop with 8-16 GB RAM and no dedicated GPU. Everything below
runs locally; once installed, nic-ai needs no internet.

## 1. Python

Install Python 3.11 or newer from <https://python.org/downloads> and tick
**Add python.exe to PATH** during setup. Then, in PowerShell:

```powershell
cd path\to\nic-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Ollama (the local model runtime)

1. Install from <https://ollama.com/download>.
2. Pull a model sized for your RAM:

```powershell
ollama pull qwen3:4b      # ~3 GB, the default - fast on CPU
# ollama pull qwen3:8b    # ~6 GB, better reasoning, noticeably slower
```

On a CPU-only machine expect roughly 5-15 tokens per second with the 4B
model. Replies are short by design, so this stays usable.

## 3. Configuration

```powershell
copy config.example.yaml config.yaml
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

Paste that value into `web.access_token` in `config.yaml`.

## 4. Check everything

```powershell
python -m nic doctor
```

Fix anything marked `FAIL`, then:

```powershell
python -m nic chat          # terminal
python -m nic serve         # web UI for your phone
python -m nic enroll        # record your voiceprint (see docs/voice-id.md)
python -m nic wake          # always listening, wake word (docs/wake-word.md)
python -m nic qr            # address + QR for the phone
python -m nic schedule list # scheduled routines (docs/routines.md)
python -m nic tools         # what it can actually do
```

`run-nic.bat` does the Ollama-plus-web-UI dance in one double-click.

## Autostart (optional)

Press `Win+R`, run `shell:startup`, and drop a shortcut to `run-nic.bat`
in the folder that opens.

Scheduled routines only fire while something is running, so if you use
them, this step is what makes "lock the phone at 11pm" actually happen.
