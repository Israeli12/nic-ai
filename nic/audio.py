"""Microphone capture and voice-activity segmentation.

The segmenter is a pure generator over frames, so the wake-word loop can
be tested with synthetic audio and no microphone. Energy-based VAD is
crude but cheap, which matters when a 4B model and Whisper already share
a CPU-only laptop.
"""

from __future__ import annotations

import queue
from collections import deque
from dataclasses import dataclass
from typing import Iterable, Iterator, Protocol

try:  # pragma: no cover - numpy is part of the voice extra
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
# int16 full scale, used to normalise RMS into 0..1.
INT16_MAX = 32768.0


class AudioUnavailable(RuntimeError):
    """The microphone could not be opened."""


class AudioSource(Protocol):
    """Anything that yields fixed-size int16 mono frames."""

    def frames(self) -> Iterator["np.ndarray"]: ...

    def close(self) -> None: ...


def require_numpy():
    if np is None:  # pragma: no cover - depends on install
        raise AudioUnavailable("Voice features need numpy; pip install numpy")
    return np


def rms(frame: "np.ndarray") -> float:
    """Normalised root-mean-square level of an int16 frame (0..1)."""
    require_numpy()
    if frame.size == 0:
        return 0.0
    samples = frame.astype(np.float32) / INT16_MAX
    return float(np.sqrt(np.mean(np.square(samples))))


@dataclass
class SegmentConfig:
    # Frames louder than noise_floor * ratio (and above floor_minimum) start
    # an utterance; quiet frames for silence_seconds end it.
    speech_ratio: float = 3.0
    floor_minimum: float = 0.012
    silence_seconds: float = 0.8
    min_utterance_seconds: float = 0.35
    max_utterance_seconds: float = 12.0
    # Keep a little audio from before the trigger so the first word survives.
    pre_roll_seconds: float = 0.3
    frame_ms: int = FRAME_MS

    def frames_for(self, seconds: float) -> int:
        return max(1, int(round(seconds * 1000 / self.frame_ms)))


def estimate_noise_floor(frames: Iterable["np.ndarray"], config: SegmentConfig | None = None) -> float:
    """Median level of a short quiet sample, used as the speech baseline."""
    require_numpy()
    levels = [rms(frame) for frame in frames]
    if not levels:
        return 0.0
    return float(np.median(levels))


def segment_utterances(
    frames: Iterable["np.ndarray"],
    config: SegmentConfig | None = None,
    noise_floor: float = 0.0,
) -> Iterator["np.ndarray"]:
    """Yield one array per detected utterance from a stream of frames."""
    require_numpy()
    config = config or SegmentConfig()
    threshold = max(noise_floor * config.speech_ratio, config.floor_minimum)
    silence_limit = config.frames_for(config.silence_seconds)
    max_frames = config.frames_for(config.max_utterance_seconds)
    min_frames = config.frames_for(config.min_utterance_seconds)

    pre_roll: deque["np.ndarray"] = deque(maxlen=config.frames_for(config.pre_roll_seconds))
    collected: list["np.ndarray"] = []
    silent_run = 0
    speaking = False

    for frame in frames:
        level = rms(frame)
        if not speaking:
            if level >= threshold:
                speaking = True
                collected = list(pre_roll)
                collected.append(frame)
                silent_run = 0
            else:
                pre_roll.append(frame)
            continue

        collected.append(frame)
        silent_run = silent_run + 1 if level < threshold else 0

        done = silent_run >= silence_limit or len(collected) >= max_frames
        if not done:
            continue
        # Trim the trailing silence we used to detect the end of speech.
        voiced = collected[: len(collected) - silent_run] if silent_run else collected
        if len(voiced) >= min_frames:
            yield np.concatenate(voiced)
        pre_roll.clear()
        collected = []
        silent_run = 0
        speaking = False

    if speaking and len(collected) >= min_frames:
        yield np.concatenate(collected)


def to_wav_bytes(samples: "np.ndarray", sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap int16 samples in a WAV container (what Whisper wants on disk)."""
    import io
    import wave

    require_numpy()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.astype(np.int16).tobytes())
    return buffer.getvalue()


class MicrophoneSource:
    """Live microphone frames via sounddevice."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, frame_ms: int = FRAME_MS, device=None):
        require_numpy()
        try:
            import sounddevice
        except ImportError as exc:  # pragma: no cover - depends on install
            raise AudioUnavailable(
                "Microphone capture needs sounddevice; pip install sounddevice"
            ) from exc
        self.sample_rate = sample_rate
        self.block = sample_rate * frame_ms // 1000
        self._queue: queue.Queue = queue.Queue()
        self._closed = False
        try:
            self._stream = sounddevice.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self.block,
                device=device,
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001 - PortAudio raises many types
            raise AudioUnavailable(f"could not open the microphone: {exc}") from exc

    def _on_audio(self, indata, _frames, _time, status) -> None:
        if status:  # pragma: no cover - hardware dependent
            # Overflows happen under load; dropping a frame beats crashing.
            pass
        self._queue.put(indata[:, 0].copy())

    def frames(self) -> Iterator["np.ndarray"]:
        while not self._closed:
            try:
                yield self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

    def close(self) -> None:
        self._closed = True
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001 - closing twice must stay harmless
            pass
