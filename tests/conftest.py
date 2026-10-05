import numpy as np
import pytest

from nic.audio import FRAME_SAMPLES, SAMPLE_RATE


@pytest.fixture()
def make_frames():
    """Build a frame stream from (level, frame_count) pairs."""

    def build(spec, seed: int = 0):
        rng = np.random.default_rng(seed)
        frames = []
        for level, count in spec:
            for _ in range(count):
                frames.append((rng.standard_normal(FRAME_SAMPLES) * level * 32768).astype(np.int16))
        return frames

    return build


@pytest.fixture()
def synth_voice():
    """A crude vowel-like signal standing in for a person's voice."""

    def build(pitch: float, seconds: float = 2.0, seed: int = 0, sample_rate: int = SAMPLE_RATE):
        rng = np.random.default_rng(seed)
        t = np.arange(int(seconds * sample_rate)) / sample_rate
        signal = np.zeros_like(t)
        for harmonic in range(1, 14):
            signal += (1.0 / harmonic) * np.sin(2 * np.pi * pitch * harmonic * t + rng.uniform(0, 6))
        for formant, gain in ((700, 0.6), (1220, 0.4), (2600, 0.2)):
            signal += gain * np.sin(2 * np.pi * formant * t + rng.uniform(0, 6))
        signal += 0.02 * rng.standard_normal(t.size)
        return (signal / np.abs(signal).max() * 20000).astype(np.int16)

    return build
