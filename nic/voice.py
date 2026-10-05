"""Offline speech in and out.

Speech-to-text uses faster-whisper (CPU, int8), text-to-speech uses Piper.
Both run locally; neither needs a network. They are optional extras, so
every import failure explains exactly what to install.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from .config import VoiceConfig, expand

SAMPLE_RATE = 16000


class VoiceUnavailable(RuntimeError):
    """A voice dependency is missing or misconfigured."""


class Transcriber:
    """Wraps faster-whisper, loading the model once and reusing it."""

    def __init__(self, config: VoiceConfig):
        self.config = config
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise VoiceUnavailable(
                    "Speech input needs faster-whisper; pip install faster-whisper"
                ) from exc
            self._model = WhisperModel(
                self.config.stt_model,
                device="cpu",
                compute_type=self.config.stt_compute_type,
            )
        return self._model

    def transcribe_file(self, path: str | Path) -> str:
        segments, _info = self._load().transcribe(str(path), beam_size=1, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()

    def record_and_transcribe(self, seconds: float = 6.0) -> str:
        """Record from the default microphone, then transcribe."""
        try:
            import sounddevice
        except ImportError as exc:
            raise VoiceUnavailable(
                "Microphone capture needs sounddevice; pip install sounddevice"
            ) from exc
        frames = sounddevice.rec(
            int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16"
        )
        sounddevice.wait()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            temp_path = Path(handle.name)
        with wave.open(str(temp_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(frames.tobytes())
        try:
            return self.transcribe_file(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)


class Speaker:
    """Speaks text with Piper, falling back to the Windows built-in voice."""

    def __init__(self, config: VoiceConfig):
        self.config = config

    def _piper_available(self) -> bool:
        return bool(self.config.piper_voice) and bool(
            shutil.which(self.config.piper_path) or Path(self.config.piper_path).exists()
        )

    def say(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._piper_available():
            self._say_with_piper(text)
            return
        self._say_with_system(text)

    def _say_with_piper(self, text: str) -> None:
        voice = expand(self.config.piper_voice)
        if not voice.exists():
            raise VoiceUnavailable(f"Piper voice not found: {voice}")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            output = Path(handle.name)
        try:
            subprocess.run(
                [self.config.piper_path, "--model", str(voice), "--output_file", str(output)],
                input=text,
                text=True,
                check=True,
                capture_output=True,
            )
            _play_wav(output)
        finally:
            output.unlink(missing_ok=True)

    def _say_with_system(self, text: str) -> None:
        # Windows ships an offline SAPI voice, which is a fine fallback.
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell:
            escaped = text.replace("'", "''")
            subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-Command",
                    "Add-Type -AssemblyName System.Speech;"
                    "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                    f".Speak('{escaped}')",
                ],
                check=False,
                capture_output=True,
            )
            return
        for candidate in ("say", "espeak-ng", "espeak"):
            if shutil.which(candidate):
                subprocess.run([candidate, text], check=False)
                return
        raise VoiceUnavailable(
            "No speech output available. Set voice.piper_voice to a Piper .onnx voice."
        )


def _play_wav(path: Path) -> None:
    try:
        import sounddevice
        import soundfile
    except ImportError:
        if shutil.which("aplay"):
            subprocess.run(["aplay", "-q", str(path)], check=False)
            return
        raise VoiceUnavailable(
            "Playing audio needs sounddevice and soundfile; pip install sounddevice soundfile"
        ) from None
    data, rate = soundfile.read(str(path), dtype="float32")
    sounddevice.play(data, rate)
    sounddevice.wait()
