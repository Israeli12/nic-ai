"""Interactive voice enrollment: teach nic-ai whose voice to obey."""

from __future__ import annotations

from typing import Callable, Iterator

import numpy as np

from .audio import MicrophoneSource, SAMPLE_RATE, SegmentConfig, estimate_noise_floor, segment_utterances
from .config import Config, expand
from .voiceid import Voiceprint, VoiceIdUnavailable, cosine, enroll, load_embedder

# Varied phonetics give the embedder more to work with than one phrase.
PHRASES = [
    "Nic, what is my phone battery at?",
    "Open my browser and check the weather.",
    "Lock the laptop in five minutes please.",
    "Send a quick message to say I am running late.",
    "Turn the volume down and pause the music.",
    "Show me the notifications on my phone.",
    "Take a screenshot of the laptop screen.",
]


def capture_utterances(
    source,
    count: int,
    segment_config: SegmentConfig,
    noise_floor: float,
    before_each: Callable[[int], None] | None = None,
) -> Iterator[np.ndarray]:
    """Yield `count` spoken utterances, prompting before each one."""
    index = 0

    def frames():
        yield from source.frames()

    stream = segment_utterances(frames(), segment_config, noise_floor)
    while index < count:
        if before_each:
            before_each(index)
        try:
            samples = next(stream)
        except StopIteration:  # pragma: no cover - the mic closed
            return
        index += 1
        yield samples


def run_enrollment(
    config: Config,
    samples_wanted: int = 5,
    label: str = "owner",
    say: Callable[[str], None] = print,
    source=None,
) -> Voiceprint:
    """Record several clips, build a voiceprint, and save it."""
    voice = config.voice
    embedder = load_embedder(voice.voice_id_engine)
    segment_config = SegmentConfig(
        speech_ratio=voice.vad_speech_ratio,
        floor_minimum=voice.vad_floor_minimum,
        silence_seconds=voice.vad_silence_seconds,
        min_utterance_seconds=max(0.8, voice.vad_min_utterance_seconds),
        max_utterance_seconds=voice.vad_max_utterance_seconds,
    )

    source = source or MicrophoneSource()
    try:
        frames = source.frames()
        say("Stay quiet for a moment while I measure the room...")
        noise_floor = estimate_noise_floor([frame for frame, _ in zip(frames, range(33))])
        say(f"noise floor {noise_floor:.4f}\n")

        say(
            f"I will record {samples_wanted} short clips. Speak normally, at the"
            " distance and in the room you will actually use.\n"
        )

        def prompt(index: int) -> None:
            say(f"[{index + 1}/{samples_wanted}] Say: \"{PHRASES[index % len(PHRASES)]}\"")

        clips = []
        for samples in capture_utterances(
            source, samples_wanted, segment_config, noise_floor, prompt
        ):
            seconds = samples.size / SAMPLE_RATE
            say(f"    captured {seconds:.1f}s")
            clips.append(samples)
    finally:
        source.close()

    if len(clips) < 2:
        raise VoiceIdUnavailable("not enough audio captured; try again in a quieter room")

    voiceprint = enroll(
        clips,
        embedder,
        threshold=voice.speaker_threshold or None,
        label=label,
    )
    path = voiceprint.save(expand(voice.voiceprint))

    scores = [cosine(row, voiceprint.centroid) for row in voiceprint.embeddings]
    say("")
    say(f"saved {path}")
    say(f"embedder   {voiceprint.embedder}")
    say(f"threshold  {voiceprint.threshold}")
    say(f"clip match {min(scores):.3f} worst, {max(scores):.3f} best")
    if voiceprint.embedder == "mfcc":
        say(
            "\nNote: the built-in MFCC embedder is the no-extra-dependency option."
            "\nFor noticeably better accuracy: pip install resemblyzer, then re-enroll."
        )
    if min(scores) < voiceprint.threshold:
        say(
            "\nWarning: your own clips vary more than the threshold allows."
            " Re-enroll in a quieter room, or lower voice.speaker_threshold."
        )
    return voiceprint
