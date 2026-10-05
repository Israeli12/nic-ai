import numpy as np
import pytest

from nic.voiceid import (
    MfccEmbedder,
    SpeakerVerifier,
    VoiceIdUnavailable,
    Voiceprint,
    cosine,
    enroll,
    load_embedder,
    mfcc,
)

OWNER_PITCH = 118.0
STRANGER_PITCH = 205.0


@pytest.fixture()
def embedder() -> MfccEmbedder:
    return MfccEmbedder()


@pytest.fixture()
def owner_print(embedder, synth_voice) -> Voiceprint:
    clips = [synth_voice(OWNER_PITCH, seed=index) for index in range(5)]
    return enroll(clips, embedder)


def test_mfcc_shape_is_frames_by_coefficients(synth_voice):
    coefficients = mfcc(synth_voice(OWNER_PITCH, seconds=1.0))
    assert coefficients.shape[1] == 20
    assert coefficients.shape[0] > 50


def test_embeddings_are_unit_length(embedder, synth_voice):
    embedding = embedder.embed(synth_voice(OWNER_PITCH))
    assert pytest.approx(1.0, abs=1e-5) == float(np.linalg.norm(embedding))


def test_too_short_a_clip_is_refused(embedder):
    with pytest.raises(VoiceIdUnavailable, match="too short"):
        embedder.embed(np.zeros(100, dtype=np.int16))


def test_enrollment_needs_more_than_one_clip(embedder, synth_voice):
    with pytest.raises(VoiceIdUnavailable, match="at least 2"):
        enroll([synth_voice(OWNER_PITCH)], embedder)


def test_owner_is_accepted(owner_print, embedder, synth_voice):
    verifier = SpeakerVerifier(owner_print, embedder)
    result = verifier.verify(synth_voice(OWNER_PITCH, seed=42))
    assert result.accepted
    assert result.score > owner_print.threshold


def test_stranger_is_rejected(owner_print, embedder, synth_voice):
    verifier = SpeakerVerifier(owner_print, embedder)
    result = verifier.verify(synth_voice(STRANGER_PITCH, seed=3))
    assert not result.accepted
    assert result.score < owner_print.threshold


def test_result_is_falsey_when_rejected(owner_print, embedder, synth_voice):
    verifier = SpeakerVerifier(owner_print, embedder)
    assert not bool(verifier.verify(synth_voice(STRANGER_PITCH, seed=4)))


def test_silence_is_not_mistaken_for_the_owner(owner_print, embedder):
    verifier = SpeakerVerifier(owner_print, embedder)
    assert not verifier.verify(np.zeros(16000, dtype=np.int16)).accepted


def test_voiceprint_round_trips_through_disk(owner_print, tmp_path, embedder, synth_voice):
    path = owner_print.save(tmp_path / "voiceprint.npz")
    loaded = Voiceprint.load(path)
    assert loaded.embedder == "mfcc"
    assert loaded.threshold == owner_print.threshold
    assert np.allclose(loaded.centroid, owner_print.centroid)
    assert SpeakerVerifier(loaded, embedder).verify(synth_voice(OWNER_PITCH, seed=9)).accepted


def test_missing_voiceprint_explains_how_to_enroll(tmp_path):
    with pytest.raises(VoiceIdUnavailable, match="nic enroll"):
        Voiceprint.load(tmp_path / "nope.npz")


def test_mismatched_embedder_is_refused(owner_print, embedder):
    owner_print.embedder = "resemblyzer"
    with pytest.raises(VoiceIdUnavailable, match="re-run"):
        SpeakerVerifier(owner_print, embedder)


def test_explicit_threshold_is_respected(embedder, synth_voice):
    clips = [synth_voice(OWNER_PITCH, seed=index) for index in range(3)]
    assert enroll(clips, embedder, threshold=0.99).threshold == 0.99


def test_cohesion_reports_the_worst_enrollment_clip(owner_print):
    assert 0.0 < owner_print.cohesion() <= 1.0


def test_adapt_moves_toward_an_accepted_clip(owner_print, embedder, synth_voice):
    verifier = SpeakerVerifier(owner_print, embedder)
    before = verifier.voiceprint.centroid.copy()
    verifier.adapt(synth_voice(OWNER_PITCH, seed=11))
    assert not np.allclose(before, verifier.voiceprint.centroid)


def test_adapt_ignores_a_rejected_clip(owner_print, embedder, synth_voice):
    verifier = SpeakerVerifier(owner_print, embedder)
    before = verifier.voiceprint.centroid.copy()
    verifier.adapt(synth_voice(STRANGER_PITCH, seed=12))
    assert np.allclose(before, verifier.voiceprint.centroid)


def test_auto_engine_falls_back_to_mfcc_without_resemblyzer():
    # Resemblyzer is not a hard dependency, so 'auto' must still work.
    assert load_embedder("auto").name in {"resemblyzer", "mfcc"}


def test_unknown_engine_is_rejected():
    with pytest.raises(VoiceIdUnavailable, match="unknown voice_id engine"):
        load_embedder("magic")


def test_cosine_of_identical_vectors_is_one():
    vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert pytest.approx(1.0, abs=1e-6) == cosine(vector, vector * 3)
