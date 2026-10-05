"""Speaker verification: only act on one person's voice.

Two embedders are supported. Resemblyzer (a trained d-vector model) is
far more accurate and is used when installed; otherwise a pure-numpy
MFCC embedder runs, which needs no torch - a real consideration on a
16 GB CPU-only laptop where torch alone is a ~2 GB install.

Read `docs/voice-id.md` before relying on this: it is a convenience
filter so the assistant ignores the TV, your family, and a podcast. It is
not an authentication mechanism - a recording of you will pass.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

import numpy as np

from .audio import SAMPLE_RATE, INT16_MAX

# Cosine-similarity defaults, tuned per embedder: the MFCC one needs a
# lower bar because its embeddings are less separable.
DEFAULT_THRESHOLDS = {"resemblyzer": 0.75, "mfcc": 0.86}


class VoiceIdUnavailable(RuntimeError):
    """Verification was requested but cannot run."""


class Embedder(Protocol):
    name: str

    def embed(self, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray: ...


def _as_float(samples: np.ndarray) -> np.ndarray:
    if samples.dtype == np.int16:
        return samples.astype(np.float32) / INT16_MAX
    return samples.astype(np.float32)


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(_unit(a), _unit(b)))


# --------------------------------------------------------------------------
# MFCC embedder (no heavy dependencies)
# --------------------------------------------------------------------------

def _mel_filterbank(num_filters: int, fft_size: int, sample_rate: int) -> np.ndarray:
    def to_mel(hz: float) -> float:
        return 2595.0 * np.log10(1.0 + hz / 700.0)

    def to_hz(mel: float) -> float:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    low, high = to_mel(80.0), to_mel(min(7600.0, sample_rate / 2))
    points = np.linspace(low, high, num_filters + 2)
    bins = np.floor((fft_size + 1) * np.array([to_hz(point) for point in points]) / sample_rate)
    bank = np.zeros((num_filters, fft_size // 2 + 1), dtype=np.float32)
    for index in range(1, num_filters + 1):
        left, centre, right = int(bins[index - 1]), int(bins[index]), int(bins[index + 1])
        centre = max(centre, left + 1)
        right = max(right, centre + 1)
        for position in range(left, min(centre, bank.shape[1])):
            bank[index - 1, position] = (position - left) / (centre - left)
        for position in range(centre, min(right, bank.shape[1])):
            bank[index - 1, position] = (right - position) / (right - centre)
    return bank


def mfcc(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    num_coefficients: int = 20,
    frame_length: int = 400,
    hop_length: int = 160,
    num_filters: int = 26,
) -> np.ndarray:
    """Mel-frequency cepstral coefficients, one row per frame."""
    audio = _as_float(samples)
    if audio.size < frame_length:
        audio = np.pad(audio, (0, frame_length - audio.size))
    # Pre-emphasis lifts the higher formants that distinguish speakers.
    audio = np.append(audio[0], audio[1:] - 0.97 * audio[:-1])
    frame_count = 1 + (audio.size - frame_length) // hop_length
    indices = np.arange(frame_length)[None, :] + hop_length * np.arange(frame_count)[:, None]
    frames = audio[indices] * np.hamming(frame_length).astype(np.float32)
    fft_size = 512
    spectrum = np.abs(np.fft.rfft(frames, n=fft_size)) ** 2 / fft_size
    bank = _mel_filterbank(num_filters, fft_size, sample_rate)
    energies = np.maximum(spectrum @ bank.T, 1e-10)
    log_energies = np.log(energies)
    # DCT-II, keeping the low-order coefficients.
    basis = np.cos(
        np.pi
        / num_filters
        * (np.arange(num_filters) + 0.5)[None, :]
        * np.arange(num_coefficients)[:, None]
    ).astype(np.float32)
    return log_energies @ basis.T


class MfccEmbedder:
    """Mean and standard deviation of MFCCs, cepstral-mean normalised."""

    name = "mfcc"

    def embed(self, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
        coefficients = mfcc(samples, sample_rate)
        if coefficients.shape[0] < 2:
            raise VoiceIdUnavailable("clip too short to identify a voice")
        # Drop c0 (loudness) so distance from the mic matters less.
        coefficients = coefficients[:, 1:]
        normalised = coefficients - coefficients.mean(axis=0, keepdims=True)
        embedding = np.concatenate(
            [coefficients.mean(axis=0), normalised.std(axis=0)]
        ).astype(np.float32)
        return _unit(embedding)


class ResemblyzerEmbedder:
    """Trained d-vector speaker encoder; much more reliable than MFCCs."""

    name = "resemblyzer"

    def __init__(self):
        try:
            from resemblyzer import VoiceEncoder
        except ImportError as exc:  # pragma: no cover - optional
            raise VoiceIdUnavailable(
                "Resemblyzer is not installed; pip install resemblyzer"
            ) from exc
        self._encoder = VoiceEncoder()

    def embed(self, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
        from resemblyzer import preprocess_wav

        wav = preprocess_wav(_as_float(samples), source_sr=sample_rate)
        return _unit(np.asarray(self._encoder.embed_utterance(wav), dtype=np.float32))


def load_embedder(preference: str = "auto") -> Embedder:
    """Pick an embedder: 'auto' prefers resemblyzer, then falls back."""
    choice = (preference or "auto").strip().lower()
    if choice in {"auto", "resemblyzer"}:
        try:
            return ResemblyzerEmbedder()
        except VoiceIdUnavailable:
            if choice == "resemblyzer":
                raise
    if choice in {"auto", "mfcc"}:
        return MfccEmbedder()
    raise VoiceIdUnavailable(f"unknown voice_id engine: {preference}")


# --------------------------------------------------------------------------
# Enrollment and verification
# --------------------------------------------------------------------------

@dataclass
class Voiceprint:
    embedder: str
    centroid: np.ndarray
    embeddings: np.ndarray
    threshold: float
    created_at: str = ""
    label: str = "owner"

    def save(self, path: str | Path) -> Path:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            target,
            centroid=self.centroid,
            embeddings=self.embeddings,
            meta=json.dumps(
                {
                    "embedder": self.embedder,
                    "threshold": self.threshold,
                    "created_at": self.created_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "label": self.label,
                }
            ),
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> "Voiceprint":
        source = Path(path).expanduser()
        if not source.exists():
            raise VoiceIdUnavailable(
                f"no voiceprint at {source}. Enroll your voice first: python -m nic enroll"
            )
        with np.load(source, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            return cls(
                embedder=meta["embedder"],
                centroid=data["centroid"],
                embeddings=data["embeddings"],
                threshold=float(meta["threshold"]),
                created_at=meta.get("created_at", ""),
                label=meta.get("label", "owner"),
            )

    def similarity(self, embedding: np.ndarray) -> float:
        return cosine(embedding, self.centroid)

    def cohesion(self) -> float:
        """Lowest similarity between an enrollment sample and the centroid."""
        if self.embeddings.shape[0] < 2:
            return 1.0
        return float(min(cosine(row, self.centroid) for row in self.embeddings))


def enroll(
    clips: Iterable[np.ndarray],
    embedder: Embedder,
    sample_rate: int = SAMPLE_RATE,
    threshold: float | None = None,
    label: str = "owner",
) -> Voiceprint:
    """Build a voiceprint from several clips of the same person speaking."""
    embeddings = [embedder.embed(clip, sample_rate) for clip in clips]
    if len(embeddings) < 2:
        raise VoiceIdUnavailable("enrollment needs at least 2 clips; 5 is better")
    matrix = np.vstack(embeddings)
    centroid = _unit(matrix.mean(axis=0))
    if threshold is None:
        base = DEFAULT_THRESHOLDS.get(embedder.name, 0.8)
        worst = min(cosine(row, centroid) for row in matrix)
        # Sit just under the least typical enrollment clip, but never above
        # the embedder's default bar - that is what keeps others out.
        threshold = round(min(base, max(0.5, worst - 0.03)), 3)
    return Voiceprint(
        embedder=embedder.name,
        centroid=centroid,
        embeddings=matrix,
        threshold=float(threshold),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        label=label,
    )


@dataclass
class VerificationResult:
    accepted: bool
    score: float
    threshold: float

    def __bool__(self) -> bool:
        return self.accepted


class SpeakerVerifier:
    """Checks a clip against the enrolled voiceprint."""

    def __init__(self, voiceprint: Voiceprint, embedder: Embedder | None = None):
        self.voiceprint = voiceprint
        self.embedder = embedder or load_embedder(voiceprint.embedder)
        if self.embedder.name != voiceprint.embedder:
            raise VoiceIdUnavailable(
                f"voiceprint was made with '{voiceprint.embedder}' but"
                f" '{self.embedder.name}' is loaded; re-run python -m nic enroll"
            )

    @classmethod
    def from_path(cls, path: str | Path, engine: str = "auto") -> "SpeakerVerifier":
        voiceprint = Voiceprint.load(path)
        return cls(voiceprint, load_embedder(voiceprint.embedder or engine))

    def verify(self, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> VerificationResult:
        try:
            embedding = self.embedder.embed(samples, sample_rate)
        except VoiceIdUnavailable:
            # Too short to judge: refuse rather than guess.
            return VerificationResult(False, 0.0, self.voiceprint.threshold)
        score = self.voiceprint.similarity(embedding)
        return VerificationResult(
            accepted=score >= self.voiceprint.threshold,
            score=round(score, 4),
            threshold=self.voiceprint.threshold,
        )

    def adapt(self, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
        """Nudge the centroid toward an accepted clip, so it tracks your voice."""
        result = self.verify(samples, sample_rate)
        if not result.accepted:
            return
        embedding = self.embedder.embed(samples, sample_rate)
        self.voiceprint.centroid = _unit(0.9 * self.voiceprint.centroid + 0.1 * embedding)
