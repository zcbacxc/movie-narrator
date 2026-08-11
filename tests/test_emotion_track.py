# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for EmotionTrack — the unified emotion value object (G5).

Verifies the value object's queryable axis and intensity tables, and that its
behaviour matches the historical per-consumer functions it now backs
(``map_segment_emotions`` / ``_compute_emotion_profile``).
"""

from movie_narrator.pipeline.bgm import _compute_emotion_profile
from movie_narrator.utils.emotion_track import (
    EMOTION_BGM_GAIN_DB,
    EMOTION_ENERGY,
    EMOTION_SPEED,
    EmotionTrack,
)
from movie_narrator.utils.prosody import map_segment_emotions


def test_from_beats_extracts_string_emotions():
    beats = [{"emotion": "intense"}, {"emotion": None}, "not-a-dict", {"emotion": "calm"}]
    track = EmotionTrack.from_beats(beats)
    assert track.emotions == ["intense", None, None, "calm"]


def test_from_metadata_reads_beats_meta():
    track = EmotionTrack.from_metadata({"beats_meta": [{"emotion": "twist"}]})
    assert track.emotions == ["twist"]
    assert EmotionTrack.from_metadata({}).empty is True
    assert EmotionTrack.from_metadata(None).empty is True


def test_empty_when_no_emotions():
    assert EmotionTrack.from_beats([]).empty is True
    assert EmotionTrack.from_beats([{}, {}]).empty is True
    assert EmotionTrack.from_beats([{"emotion": None}]).empty is True


def test_emotion_index_access():
    track = EmotionTrack.from_beats([{"emotion": "intense"}, {"emotion": "calm"}])
    assert track.emotion(0) == "intense"
    assert track.emotion(1) == "calm"
    assert track.emotion(5) is None
    assert track.emotion(-1) is None


def test_segment_emotions_matches_map_segment_emotions():
    cases = [
        (5, None),
        (3, [{"emotion": "intense"}, {"emotion": "calm"}, {"emotion": "suspense"}]),
        (4, [{"emotion": "intense"}, {"emotion": "calm"}]),
        (3, [{"emotion": "intense"}, {}, {"emotion": "calm"}]),
        (3, [{}, {}, {}]),
    ]
    for n, beats in cases:
        track_result = EmotionTrack.from_beats(beats).segment_emotions(n)
        legacy_result = map_segment_emotions(n, beats)
        assert track_result == legacy_result


def test_distribution_matches_compute_emotion_profile():
    cases = [
        [],
        [{"emotion": None}, {"text": "b"}],
        [{"emotion": "intense"}, {"emotion": "intense"}],
        [{"emotion": "intense"}, {"emotion": "intense"}, {"emotion": "calm"}],
        ["not-a-dict", None, 42, {"emotion": "calm"}],
    ]
    for beats in cases:
        track_dist = EmotionTrack.from_beats(beats).distribution()
        legacy_dist = _compute_emotion_profile(beats)
        assert track_dist == legacy_dist


def test_weighted_energy_intense_profile():
    track = EmotionTrack.from_beats([{"emotion": "intense"}])
    assert abs(track.weighted_energy() - EMOTION_ENERGY["intense"]) < 1e-9
    assert EmotionTrack.from_beats([]).weighted_energy() == 0.0


def test_intensity_tables_have_shared_axis():
    # Every emotion with a speed profile also has an energy + bgm gain profile.
    assert set(EMOTION_SPEED) == set(EMOTION_ENERGY) == set(EMOTION_BGM_GAIN_DB)
    assert set(EMOTION_SPEED) == {"intense", "suspense", "calm", "twist", "laughter"}


def test_static_intensity_queries():
    assert EmotionTrack.energy("intense") == EMOTION_ENERGY["intense"]
    assert EmotionTrack.energy(None) == 0.5
    assert EmotionTrack.energy("unknown") == 0.5
    assert EmotionTrack.speed(None) == 1.0
    assert EmotionTrack.speed("unknown") == 1.0
    assert EmotionTrack.bgm_gain_db("calm") == EMOTION_BGM_GAIN_DB["calm"]
    assert EmotionTrack.bgm_gain_db(None) == 0.0
