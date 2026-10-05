import numpy as np

from nic.audio import (
    FRAME_SAMPLES,
    SAMPLE_RATE,
    SegmentConfig,
    estimate_noise_floor,
    rms,
    segment_utterances,
    to_wav_bytes,
)

QUIET = 0.001
LOUD = 0.2


def config(**kwargs) -> SegmentConfig:
    defaults = dict(silence_seconds=0.3, min_utterance_seconds=0.1, pre_roll_seconds=0.09)
    defaults.update(kwargs)
    return SegmentConfig(**defaults)


def test_rms_tracks_loudness():
    silence = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    loud = np.full(FRAME_SAMPLES, 16000, dtype=np.int16)
    assert rms(silence) == 0.0
    assert 0.4 < rms(loud) < 0.6


def test_noise_floor_is_the_median_level(make_frames):
    floor = estimate_noise_floor(make_frames([(QUIET, 20)]))
    assert 0 < floor < 0.01


def test_single_utterance_is_segmented(make_frames):
    frames = make_frames([(QUIET, 10), (LOUD, 20), (QUIET, 20)])
    found = list(segment_utterances(frames, config(), noise_floor=QUIET))
    assert len(found) == 1
    # 20 speech frames plus pre-roll, at 30ms each.
    assert 0.6 <= found[0].size / SAMPLE_RATE <= 1.0


def test_two_utterances_are_split(make_frames):
    frames = make_frames([(QUIET, 10), (LOUD, 20), (QUIET, 20), (LOUD, 15), (QUIET, 20)])
    assert len(list(segment_utterances(frames, config(), noise_floor=QUIET))) == 2


def test_a_short_blip_is_ignored(make_frames):
    frames = make_frames([(QUIET, 10), (LOUD, 1), (QUIET, 20)])
    assert list(segment_utterances(frames, config(min_utterance_seconds=0.5), QUIET)) == []


def test_silence_alone_yields_nothing(make_frames):
    assert list(segment_utterances(make_frames([(QUIET, 50)]), config(), QUIET)) == []


def test_long_speech_is_capped(make_frames):
    frames = make_frames([(LOUD, 200)])
    found = list(segment_utterances(frames, config(max_utterance_seconds=1.0), QUIET))
    assert found and all(clip.size / SAMPLE_RATE <= 1.2 for clip in found)


def test_trailing_speech_is_flushed_at_end_of_stream(make_frames):
    frames = make_frames([(QUIET, 5), (LOUD, 20)])
    assert len(list(segment_utterances(frames, config(), QUIET))) == 1


def test_loud_room_raises_the_bar(make_frames):
    # Speech only slightly above a noisy floor should not trigger.
    frames = make_frames([(0.05, 30)])
    assert list(segment_utterances(frames, config(), noise_floor=0.05)) == []


def test_wav_round_trips():
    import io
    import wave

    samples = np.arange(-1000, 1000, dtype=np.int16)
    with wave.open(io.BytesIO(to_wav_bytes(samples)), "rb") as wav:
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnchannels() == 1
        assert np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).size == samples.size
