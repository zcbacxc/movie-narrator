"""Tests for emotion-weighted BGM selection (NA-M4-S1+).

Verifies that:
1. _compute_emotion_profile returns normalised distribution
2. _score_bgm_candidate scores by mood match fraction
3. _score_bgm_candidate adds energy alignment bonus
4. select_bgm_by_emotion picks best-scoring candidate, not just first match
5. select_bgm_by_emotion stores selection metadata in ctx
6. Fallback behavior preserved when no matches
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from movie_narrator.pipeline.bgm import (
    _compute_emotion_profile,
    _score_bgm_candidate,
    select_bgm_by_emotion,
    _EMOTION_ENERGY,
)


class TestComputeEmotionProfile:
    def test_empty_beats_returns_none(self):
        assert _compute_emotion_profile([]) is None

    def test_no_emotions_returns_none(self):
        beats = [{"text": "a", "emotion": None}, {"text": "b"}]
        assert _compute_emotion_profile(beats) is None

    def test_single_emotion(self):
        beats = [{"emotion": "intense"}, {"emotion": "intense"}]
        profile = _compute_emotion_profile(beats)
        assert profile == {"intense": 1.0}

    def test_mixed_emotions_normalised(self):
        beats = [
            {"emotion": "intense"},
            {"emotion": "intense"},
            {"emotion": "calm"},
        ]
        profile = _compute_emotion_profile(beats)
        assert profile["intense"] == 2 / 3
        assert profile["calm"] == 1 / 3
        assert abs(sum(profile.values()) - 1.0) < 1e-9

    def test_all_emotions(self):
        beats = [
            {"emotion": "suspense"},
            {"emotion": "laughter"},
            {"emotion": "intense"},
            {"emotion": "calm"},
            {"emotion": "twist"},
        ]
        profile = _compute_emotion_profile(beats)
        assert len(profile) == 5
        for v in profile.values():
            assert v == 0.2

    def test_ignores_non_dict_entries(self):
        beats = ["not a dict", None, 42, {"emotion": "calm"}]
        profile = _compute_emotion_profile(beats)
        assert profile == {"calm": 1.0}


class TestScoreBgmCandidate:
    def test_no_mood_match_returns_zero(self):
        profile = {"intense": 1.0}
        sample = {"mood": "calm", "energy": 0.2}
        assert _score_bgm_candidate(sample, profile) == 0.0

    def test_perfect_mood_match(self):
        profile = {"intense": 1.0}
        sample = {"mood": "intense"}
        score = _score_bgm_candidate(sample, profile)
        assert score > 0.0
        assert score >= 1.0  # at least the primary fraction

    def test_energy_alignment_bonus(self):
        profile = {"intense": 1.0}  # energy ~0.9
        sample_high_energy = {"mood": "intense", "energy": 0.9}
        sample_low_energy = {"mood": "intense", "energy": 0.1}
        score_high = _score_bgm_candidate(sample_high_energy, profile)
        score_low = _score_bgm_candidate(sample_low_energy, profile)
        assert score_high > score_low

    def test_dominant_emotion_scores_higher(self):
        profile = {"intense": 0.7, "calm": 0.3}
        sample_intense = {"mood": "intense"}
        sample_calm = {"mood": "calm"}
        assert _score_bgm_candidate(sample_intense, profile) > _score_bgm_candidate(sample_calm, profile)

    def test_mood_not_in_profile_returns_zero(self):
        profile = {"calm": 1.0}
        sample = {"mood": "nonexistent_emotion"}
        assert _score_bgm_candidate(sample, profile) == 0.0


class TestSelectBgmByEmotion:
    def _make_ctx_with_metadata(self, beats_meta, samples, tmpdir):
        """Build a mock ctx with BGM metadata file."""
        metadata_path = Path(tmpdir) / "bgm_metadata.yaml"
        import yaml
        metadata_path.write_text(
            yaml.dump({"bgm_samples": samples}),
            encoding="utf-8",
        )
        # Create the dummy BGM files
        for s in samples:
            fname = s.get("filename", "")
            if fname:
                (Path(tmpdir) / fname).write_bytes(b"\x00")

        ctx = MagicMock()
        ctx.metadata = {
            "beats_meta": beats_meta,
            "bgm_metadata_path": str(metadata_path),
        }
        return ctx

    def test_selects_best_matching_bgm(self, tmp_path):
        beats = [{"emotion": "intense"}, {"emotion": "intense"}, {"emotion": "calm"}]
        samples = [
            {"filename": "calm_bgm.mp3", "mood": "calm", "energy": 0.2},
            {"filename": "intense_bgm.mp3", "mood": "intense", "energy": 0.8},
        ]
        ctx = self._make_ctx_with_metadata(beats, samples, tmp_path)
        result = select_bgm_by_emotion(ctx)
        assert "intense_bgm.mp3" in result

    def test_stores_selection_metadata(self, tmp_path):
        beats = [{"emotion": "intense"}, {"emotion": "calm"}]
        samples = [
            {"filename": "intense_bgm.mp3", "mood": "intense", "energy": 0.8},
        ]
        ctx = self._make_ctx_with_metadata(beats, samples, tmp_path)
        select_bgm_by_emotion(ctx)
        assert "bgm_selection" in ctx.metadata
        assert "emotion_profile" in ctx.metadata["bgm_selection"]
        assert "score" in ctx.metadata["bgm_selection"]

    def test_no_matching_emotion_returns_none(self, tmp_path):
        beats = [{"emotion": "twist"}]
        samples = [
            {"filename": "calm_bgm.mp3", "mood": "calm"},
            {"filename": "intense_bgm.mp3", "mood": "intense"},
        ]
        ctx = self._make_ctx_with_metadata(beats, samples, tmp_path)
        result = select_bgm_by_emotion(ctx)
        assert result is None

    def test_empty_beats_meta_returns_none(self, tmp_path):
        ctx = MagicMock()
        ctx.metadata = {"beats_meta": [], "bgm_metadata_path": str(tmp_path / "x.yaml")}
        assert select_bgm_by_emotion(ctx) is None

    def test_missing_metadata_file_returns_none(self, tmp_path):
        ctx = MagicMock()
        ctx.metadata = {
            "beats_meta": [{"emotion": "intense"}],
            "bgm_metadata_path": str(tmp_path / "nonexistent.yaml"),
        }
        assert select_bgm_by_emotion(ctx) is None

    def test_picks_higher_energy_when_dominant_is_intense(self, tmp_path):
        """Two intense BGMs: one with matching energy, one without."""
        beats = [{"emotion": "intense"}] * 4 + [{"emotion": "suspense"}]
        samples = [
            {"filename": "low_energy_intense.mp3", "mood": "intense", "energy": 0.1},
            {"filename": "high_energy_intense.mp3", "mood": "intense", "energy": 0.9},
        ]
        ctx = self._make_ctx_with_metadata(beats, samples, tmp_path)
        result = select_bgm_by_emotion(ctx)
        assert "high_energy_intense.mp3" in result
