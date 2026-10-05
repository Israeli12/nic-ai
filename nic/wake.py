"""Always-listening wake-word loop.

Pipeline per utterance:

    mic -> VAD segment -> speaker check -> transcribe -> wake word? -> agent

The speaker check runs *before* transcription, so audio from anyone who
is not the enrolled owner is discarded without ever being turned into
text, and without waking the model.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, Protocol

import numpy as np

from .agent import Agent
from .audio import (
    AudioSource,
    MicrophoneSource,
    SAMPLE_RATE,
    SegmentConfig,
    estimate_noise_floor,
    segment_utterances,
)
from .config import Config, expand
from .llm import ModelUnavailable
from .voiceid import SpeakerVerifier, VoiceIdUnavailable

GREETINGS = {"hey", "hi", "hello", "ok", "okay", "yo", "hey there"}
YES_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "allow", "do it", "go ahead", "confirm"}
STOP_WORDS = {"stop listening", "go to sleep", "never mind", "nevermind", "cancel", "stand down"}


class Transcribes(Protocol):
    def transcribe_samples(self, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> str: ...


@dataclass
class HeardUtterance:
    """One segment of audio that made it through the gates."""

    text: str
    command: str
    woke: bool
    speaker_score: float = 1.0
    accepted: bool = True
    reason: str = ""


def normalise(text: str) -> str:
    """Lowercase, drop punctuation, and collapse runs of whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", text.lower())).strip()


def _edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 3
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        current = [i]
        for j, right in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right))
            )
        previous = current
    return previous[-1]


@dataclass
class WakeWordMatcher:
    """Finds the wake word in a transcript and returns what follows it."""

    wake_word: str = "nic"
    variants: list[str] = field(default_factory=list)
    # Whisper often writes a short name slightly wrong; allow one typo.
    max_typos: int = 1

    def accepted_forms(self) -> set[str]:
        forms = {normalise(self.wake_word)}
        forms.update(normalise(variant) for variant in self.variants)
        return {form for form in forms if form}

    def match(self, text: str) -> tuple[bool, str]:
        """Return (woke, command). The command may be empty."""
        words = normalise(text).split()
        if not words:
            return False, ""
        forms = self.accepted_forms()
        # The name must open the sentence, optionally after a greeting:
        # "I told Nick about it" is talking about someone, not to us.
        for index, word in enumerate(words[:3]):
            if index > 0 and words[index - 1] not in GREETINGS:
                break
            if word in forms or any(
                _edit_distance(word, form) <= self.max_typos for form in forms
            ):
                remainder = words[index + 1 :]
                # Drop a leading filler like "hey nic, please ..."
                while remainder and remainder[0] in {"please", "can", "could", "you"}:
                    remainder = remainder[1:]
                return True, " ".join(remainder).strip()
        return False, ""


class OpenWakeWordGate:
    """Optional low-CPU wake detection on raw frames."""

    def __init__(self, model_path: str, threshold: float = 0.5):
        try:
            from openwakeword.model import Model
        except ImportError as exc:  # pragma: no cover - optional
            raise VoiceIdUnavailable(
                "wake_engine 'openwakeword' needs the openwakeword package;"
                " pip install openwakeword"
            ) from exc
        kwargs = {"wakeword_models": [model_path]} if model_path else {}
        self._model = Model(**kwargs)
        self.threshold = threshold

    def triggered(self, frame: np.ndarray) -> bool:
        scores = self._model.predict(frame)
        return any(score >= self.threshold for score in scores.values())


class WakeLoop:
    """Listens continuously, acting only on the enrolled owner's commands."""

    def __init__(
        self,
        config: Config,
        agent: Agent,
        transcriber: Transcribes,
        source: AudioSource | None = None,
        verifier: SpeakerVerifier | None = None,
        speaker=None,
        on_event: Callable[[str], None] | None = None,
    ):
        self.config = config
        self.voice = config.voice
        self.agent = agent
        self.transcriber = transcriber
        self.source = source
        self.verifier = verifier
        self.speaker = speaker
        self.on_event = on_event or (lambda message: None)
        self.matcher = WakeWordMatcher(self.voice.wake_word, list(self.voice.wake_variants))
        self.segment_config = SegmentConfig(
            speech_ratio=self.voice.vad_speech_ratio,
            floor_minimum=self.voice.vad_floor_minimum,
            silence_seconds=self.voice.vad_silence_seconds,
            min_utterance_seconds=self.voice.vad_min_utterance_seconds,
            max_utterance_seconds=self.voice.vad_max_utterance_seconds,
        )
        self._stop = threading.Event()
        self._awake_until = 0.0
        # A dangerous action waiting for a spoken yes.
        self._pending = None

    # -- helpers ---------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def _say(self, text: str) -> None:
        self.on_event(f"nic> {text}")
        if self.speaker and self.voice.speak_replies:
            try:
                self.speaker.say(text)
            except Exception as exc:  # noqa: BLE001 - never die on audio output
                self.on_event(f"(could not speak: {exc})")

    def _awake(self) -> bool:
        return time.monotonic() < self._awake_until

    def _extend_follow_up(self) -> None:
        if self.voice.follow_up_seconds > 0:
            self._awake_until = time.monotonic() + self.voice.follow_up_seconds

    # -- the gates -------------------------------------------------------

    def classify(self, samples: np.ndarray) -> HeardUtterance:
        """Apply the speaker and wake-word gates to one utterance."""
        score = 1.0
        if self.verifier is not None:
            result = self.verifier.verify(samples)
            score = result.score
            if not result.accepted:
                return HeardUtterance(
                    text="",
                    command="",
                    woke=False,
                    speaker_score=score,
                    accepted=False,
                    reason=f"different voice (similarity {score:.2f} < {result.threshold:.2f})",
                )
            if self.voice.adapt_voiceprint:
                self.verifier.adapt(samples)

        text = self.transcriber.transcribe_samples(samples).strip()
        if not text:
            return HeardUtterance("", "", False, score, False, "nothing intelligible")

        woke, command = self.matcher.match(text)
        if woke:
            return HeardUtterance(text, command, True, score)
        if self._awake() or self._pending is not None:
            # Mid-conversation: no need to say the name again.
            return HeardUtterance(text, normalise(text), False, score)
        return HeardUtterance(text, "", False, score, False, "no wake word")

    # -- the loop --------------------------------------------------------

    def utterances(self) -> Iterator[np.ndarray]:
        source = self.source or MicrophoneSource()
        self.source = source
        frames = source.frames()
        # Sample the room so the VAD threshold suits this environment.
        calibration = [frame for frame, _ in zip(frames, range(16))]
        noise_floor = estimate_noise_floor(calibration)
        self.on_event(f"(noise floor {noise_floor:.4f})")

        def remaining() -> Iterator[np.ndarray]:
            for frame in frames:
                if self._stop.is_set():
                    return
                yield frame

        yield from segment_utterances(remaining(), self.segment_config, noise_floor)

    def handle(self, heard: HeardUtterance) -> None:
        """Act on one accepted utterance."""
        command = heard.command.strip()

        if self._pending is not None:
            tool_name, arguments = self._pending
            self._pending = None
            if any(word in command for word in YES_WORDS):
                turn = self.agent.run_approved(tool_name, arguments)
                self._report(turn)
            else:
                self._say("Cancelled.")
            self._extend_follow_up()
            return

        if command in STOP_WORDS or any(command.startswith(word) for word in STOP_WORDS):
            self._awake_until = 0.0
            self._say("Going quiet. Say my name when you need me.")
            return

        if not command:
            # Woken by name alone: open a follow-up window and prompt.
            self._extend_follow_up()
            self._say(self.voice.acknowledgement)
            return

        self.on_event(f"you> {command}")
        try:
            turn = self.agent.ask(command)
        except ModelUnavailable as exc:
            self._say(f"The local model is not responding. {exc}")
            return
        self._report(turn)
        self._extend_follow_up()

    def _report(self, turn) -> None:
        for step in turn.steps:
            marker = "." if step.ok else "!"
            self.on_event(f"  {marker} {step.tool} -> {step.result[:160]}")
        if turn.pending is not None:
            self._pending = (turn.pending.tool_name, turn.pending.arguments)
            self._say(f"{turn.pending.summary}. Say yes to allow it.")
            self._extend_follow_up()
            return
        self._say(turn.reply)

    def run(self, max_utterances: int | None = None) -> int:
        """Listen until stopped. Returns the number of utterances handled."""
        handled = 0
        self.on_event(
            f"Listening for '{self.voice.wake_word}'."
            + (" Owner voice only." if self.verifier else " Any voice (no voiceprint enrolled).")
        )
        for samples in self.utterances():
            if self._stop.is_set():
                break
            heard = self.classify(samples)
            if not heard.accepted:
                # Ignored on purpose; logged so you can tune thresholds.
                self.on_event(f"(ignored: {heard.reason})")
                continue
            self.handle(heard)
            handled += 1
            if max_utterances is not None and handled >= max_utterances:
                break
        return handled


def load_verifier(config: Config, on_event: Callable[[str], None] | None = None) -> SpeakerVerifier | None:
    """Load the voiceprint, or explain why there is none."""
    notify = on_event or (lambda message: None)
    path = expand(config.voice.voiceprint)
    if not path.exists():
        if config.voice.require_enrolled_voice:
            raise VoiceIdUnavailable(
                f"voice.require_enrolled_voice is on but no voiceprint exists at {path}."
                " Run: python -m nic enroll"
            )
        notify("(no voiceprint: the assistant will respond to any voice)")
        return None
    verifier = SpeakerVerifier.from_path(path, config.voice.voice_id_engine)
    if config.voice.speaker_threshold > 0:
        verifier.voiceprint.threshold = config.voice.speaker_threshold
    notify(
        f"(voiceprint '{verifier.voiceprint.label}' via {verifier.voiceprint.embedder},"
        f" threshold {verifier.voiceprint.threshold})"
    )
    return verifier
