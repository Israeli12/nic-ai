import numpy as np
import pytest

from nic.agent import Agent
from nic.audio import FRAME_SAMPLES
from nic.config import Config
from nic.llm import ModelReply, ToolCall
from nic.tools.registry import ToolRegistry
from nic.voiceid import MfccEmbedder, SpeakerVerifier, enroll
from nic.wake import WakeLoop, WakeWordMatcher, normalise

OWNER_PITCH = 118.0
STRANGER_PITCH = 205.0


# --- wake word matching ---------------------------------------------------

@pytest.fixture()
def matcher() -> WakeWordMatcher:
    return WakeWordMatcher("nic", ["nick", "nik"])


@pytest.mark.parametrize(
    "text,command",
    [
        ("Nic, lock my phone", "lock my phone"),
        ("nic open spotify", "open spotify"),
        ("Hey Nic, what is my battery?", "what is my battery"),
        ("okay nick take a screenshot", "take a screenshot"),
        ("Nik please open chrome", "open chrome"),
        ("Nic.", ""),
    ],
)
def test_wake_word_is_found_and_stripped(matcher, text, command):
    woke, parsed = matcher.match(text)
    assert woke
    assert parsed == command


@pytest.mark.parametrize(
    "text",
    [
        "play some music",
        "I told Nick about it yesterday",
        "so I said nic is offline now",
        "",
        "...",
    ],
)
def test_other_speech_does_not_wake(matcher, text):
    assert matcher.match(text) == (False, "")


def test_normalise_strips_punctuation_and_case():
    assert normalise("Nic, LOCK my phone!") == "nic lock my phone"


# --- the loop -------------------------------------------------------------

class FakeTranscriber:
    """Returns a queued transcript per utterance."""

    def __init__(self, transcripts):
        self.transcripts = list(transcripts)
        self.calls = 0

    def transcribe_samples(self, samples, sample_rate=16000):
        self.calls += 1
        return self.transcripts.pop(0) if self.transcripts else ""


class FakeSource:
    def __init__(self, frames):
        self._frames = frames
        self.closed = False

    def frames(self):
        yield from self._frames

    def close(self):
        self.closed = True


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)


class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)

    def chat(self, messages, tools=None):
        return self.replies.pop(0) if self.replies else ModelReply("ok", [])


@pytest.fixture()
def registry() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.tool("battery", "Battery")
    def battery() -> str:
        return "64 percent"

    @registry.tool("lock", "Lock the phone", dangerous=True)
    def lock() -> str:
        return "phone locked"

    return registry


@pytest.fixture()
def config(tmp_path) -> Config:
    config = Config()
    config.safety.audit_log = str(tmp_path / "audit.log")
    config.voice.follow_up_seconds = 0.0
    return config


def speech_frames(count: int, seed: int = 0):
    rng = np.random.default_rng(seed)
    return [(rng.standard_normal(FRAME_SAMPLES) * 0.2 * 32768).astype(np.int16) for _ in range(count)]


def quiet_frames(count: int):
    return [np.zeros(FRAME_SAMPLES, dtype=np.int16) for _ in range(count)]


def build_loop(config, registry, transcripts, replies, verifier=None, utterance_count=1):
    frames = quiet_frames(20)
    for index in range(utterance_count):
        frames += speech_frames(40, seed=index) + quiet_frames(40)
    agent = Agent(config, client=ScriptedClient(replies), registry=registry)
    speaker = FakeSpeaker()
    events: list[str] = []
    loop = WakeLoop(
        config,
        agent=agent,
        transcriber=FakeTranscriber(transcripts),
        source=FakeSource(frames),
        verifier=verifier,
        speaker=speaker,
        on_event=events.append,
    )
    return loop, speaker, events


def test_command_after_wake_word_reaches_the_agent(config, registry):
    loop, speaker, _ = build_loop(
        config,
        registry,
        ["nic what is my phone battery"],
        [ModelReply("", [ToolCall("battery", {})]), ModelReply("Sixty four percent.", [])],
    )
    assert loop.run(max_utterances=1) == 1
    assert speaker.said == ["Sixty four percent."]


def test_speech_without_the_wake_word_is_ignored(config, registry):
    loop, speaker, events = build_loop(
        config, registry, ["the weather today is fine"], [ModelReply("should not run", [])]
    )
    loop.run(max_utterances=1)
    assert speaker.said == []
    assert any("no wake word" in event for event in events)


def test_wake_word_alone_acknowledges_and_opens_a_window(config, registry):
    config.voice.follow_up_seconds = 30.0
    loop, speaker, _ = build_loop(config, registry, ["Nic?"], [])
    loop.run(max_utterances=1)
    assert speaker.said == [config.voice.acknowledgement]
    assert loop._awake()


def test_follow_up_needs_no_wake_word(config, registry):
    config.voice.follow_up_seconds = 30.0
    loop, speaker, _ = build_loop(
        config,
        registry,
        ["nic", "what is my battery"],
        [ModelReply("", [ToolCall("battery", {})]), ModelReply("Sixty four percent.", [])],
        utterance_count=2,
    )
    loop.run(max_utterances=2)
    assert speaker.said == [config.voice.acknowledgement, "Sixty four percent."]


def test_stop_phrase_closes_the_follow_up_window(config, registry):
    config.voice.follow_up_seconds = 30.0
    loop, speaker, _ = build_loop(config, registry, ["nic", "stop listening"], [], utterance_count=2)
    loop.run(max_utterances=2)
    assert not loop._awake()
    assert "quiet" in speaker.said[-1].lower()


def test_dangerous_action_waits_for_a_spoken_yes(config, registry):
    loop, speaker, _ = build_loop(
        config,
        registry,
        ["nic lock my phone", "yes"],
        [ModelReply("", [ToolCall("lock", {})]), ModelReply("Locked.", [])],
        utterance_count=2,
    )
    loop.run(max_utterances=2)
    assert "Say yes to allow it" in speaker.said[0]
    assert speaker.said[-1] == "Locked."


def test_dangerous_action_is_cancelled_without_a_yes(config, registry):
    loop, speaker, _ = build_loop(
        config,
        registry,
        ["nic lock my phone", "no leave it"],
        [ModelReply("", [ToolCall("lock", {})])],
        utterance_count=2,
    )
    loop.run(max_utterances=2)
    assert speaker.said[-1] == "Cancelled."


def test_another_voice_is_dropped_before_transcription(config, registry, synth_voice):
    embedder = MfccEmbedder()
    voiceprint = enroll([synth_voice(OWNER_PITCH, seed=i) for i in range(5)], embedder)
    verifier = SpeakerVerifier(voiceprint, embedder)

    agent = Agent(config, client=ScriptedClient([]), registry=registry)
    transcriber = FakeTranscriber(["nic lock my phone"])
    events: list[str] = []
    loop = WakeLoop(
        config,
        agent=agent,
        transcriber=transcriber,
        source=FakeSource([]),
        verifier=verifier,
        speaker=FakeSpeaker(),
        on_event=events.append,
    )

    heard = loop.classify(synth_voice(STRANGER_PITCH, seed=5))
    assert not heard.accepted
    assert "different voice" in heard.reason
    # The gate must come before Whisper, so the stranger is never transcribed.
    assert transcriber.calls == 0


def test_owner_voice_passes_the_gate(config, registry, synth_voice):
    embedder = MfccEmbedder()
    voiceprint = enroll([synth_voice(OWNER_PITCH, seed=i) for i in range(5)], embedder)
    verifier = SpeakerVerifier(voiceprint, embedder)

    agent = Agent(config, client=ScriptedClient([ModelReply("Done.", [])]), registry=registry)
    loop = WakeLoop(
        config,
        agent=agent,
        transcriber=FakeTranscriber(["nic lock the laptop"]),
        source=FakeSource([]),
        verifier=verifier,
        speaker=FakeSpeaker(),
    )
    heard = loop.classify(synth_voice(OWNER_PITCH, seed=77))
    assert heard.accepted and heard.woke
    assert heard.command == "lock the laptop"


def test_unintelligible_audio_is_ignored(config, registry):
    loop, speaker, events = build_loop(config, registry, ["   "], [])
    loop.run(max_utterances=1)
    assert speaker.said == []
    assert any("nothing intelligible" in event for event in events)


def test_stop_ends_the_loop(config, registry):
    loop, _, _ = build_loop(config, registry, ["nic hello"], [ModelReply("hi", [])])
    loop.stop()
    assert loop.run() == 0
