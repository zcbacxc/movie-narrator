"""Tests for v0.5.8 script quality improvements.

Covers:
1. BGM versatility bonus fix (_score_bgm_candidate)
2. Multilingual anti-AI tone (ANTI_AI_TONE)
3. Judge 5 dimensions (_DEFAULT_PASS_SCORE, verdict logic)
4. Beat deduplication (_deduplicate_beats)
5. Built-in hook template library (build_hook_hint fallback)
6. Script-level QA gate (validate_script_quality)
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Context, Services, ScriptSegment
from movie_narrator.pipeline.bgm import _score_bgm_candidate
from movie_narrator.pipeline.script import (
    _DEFAULT_PASS_SCORE,
    _deduplicate_beats,
    _char_bigrams,
    _jaccard_similarity,
    validate_script_quality,
)
from movie_narrator.utils.prompts import (
    ANTI_AI_TONE,
    DEFAULT_HOOK_TEMPLATES,
    JUDGE_PROMPT,
    build_hook_hint,
    build_judge_feedback_hint,
)


# ── 1. BGM versatility bonus fix ──────────────────────────


class TestBgmVersatilityBonus:
    def test_versatility_bonus_applied_for_secondary_emotions(self):
        """BGM matching dominant emotion gets versatility bonus when
        secondary emotions > 20% are present.

        Before the fix, the condition `emo == mood and frac > 0.20`
        was always False because `emo == mood` was already skipped
        by `continue`.  After the fix, the condition is just
        `frac > 0.20`.
        """
        profile = {"intense": 0.6, "calm": 0.4}
        sample = {"mood": "intense"}
        score = _score_bgm_candidate(sample, profile)
        # primary = 0.6, versatility = 0.4 * 0.15 = 0.06
        # total = 0.66 (without energy since no energy field)
        assert score == pytest.approx(0.66, abs=0.01)

    def test_no_versatility_when_single_emotion(self):
        """Single-emotion profile → no versatility bonus."""
        profile = {"intense": 1.0}
        sample = {"mood": "intense"}
        score = _score_bgm_candidate(sample, profile)
        # primary = 1.0, no secondary emotions → no versatility
        assert score == pytest.approx(1.0, abs=0.01)

    def test_versatility_skips_small_secondary_emotions(self):
        """Secondary emotions <= 20% don't get versatility bonus."""
        profile = {"intense": 0.9, "calm": 0.1}
        sample = {"mood": "intense"}
        score = _score_bgm_candidate(sample, profile)
        # primary = 0.9, calm frac=0.1 <= 0.20 → no versatility
        assert score == pytest.approx(0.9, abs=0.01)

    def test_versatility_scales_with_secondary_fraction(self):
        """Versatility bonus increases total score vs same primary without secondary."""
        profile_with_secondary = {"intense": 0.5, "calm": 0.5}
        profile_without = {"intense": 0.5}
        sample = {"mood": "intense"}
        score_with = _score_bgm_candidate(sample, profile_with_secondary)
        score_without = _score_bgm_candidate(sample, profile_without)
        # With secondary: 0.5 + 0.5*0.15 = 0.575
        # Without: 0.5
        assert score_with > score_without


# ── 2. Multilingual anti-AI tone ──────────────────────────


class TestMultilingualAntiAiTone:
    def test_contains_chinese_phrases(self):
        assert "总的来说" in ANTI_AI_TONE
        assert "值得一提的是" in ANTI_AI_TONE
        assert "综上所述" in ANTI_AI_TONE

    def test_contains_english_phrases(self):
        assert "In conclusion" in ANTI_AI_TONE
        assert "It's worth noting that" in ANTI_AI_TONE
        assert "Furthermore" in ANTI_AI_TONE

    def test_contains_japanese_phrases(self):
        assert "まとめると" in ANTI_AI_TONE
        assert "注目すべきは" in ANTI_AI_TONE

    def test_contains_korean_phrases(self):
        assert "요약하자면" in ANTI_AI_TONE
        assert "주목할 점은" in ANTI_AI_TONE

    def test_contains_repetitive_structure_rule(self):
        assert "repetitive sentence structures" in ANTI_AI_TONE.lower()


# ── 3. Judge 5 dimensions ─────────────────────────────────


class TestJudgeFiveDimensions:
    def test_default_pass_score_has_five_dimensions(self):
        assert "hook_strength" in _DEFAULT_PASS_SCORE
        assert "spoiler_level" in _DEFAULT_PASS_SCORE
        assert "plot_accuracy" in _DEFAULT_PASS_SCORE
        assert "anti_ai_compliance" in _DEFAULT_PASS_SCORE
        assert "narrative_adherence" in _DEFAULT_PASS_SCORE
        assert _DEFAULT_PASS_SCORE["verdict"] == "pass"

    def test_judge_prompt_mentions_all_five_dimensions(self):
        assert "hook_strength" in JUDGE_PROMPT
        assert "spoiler_level" in JUDGE_PROMPT
        assert "plot_accuracy" in JUDGE_PROMPT
        assert "anti_ai_compliance" in JUDGE_PROMPT
        assert "narrative_adherence" in JUDGE_PROMPT

    def test_judge_prompt_decision_rule_includes_new_dimensions(self):
        assert "anti_ai_compliance >= 6" in JUDGE_PROMPT
        assert "narrative_adherence >= 5" in JUDGE_PROMPT

    def test_judge_feedback_hint_handles_anti_ai(self):
        scores = {
            "hook_strength": 8,
            "spoiler_level": 3,
            "plot_accuracy": 9,
            "anti_ai_compliance": 3,
            "narrative_adherence": 7,
            "verdict": "retry",
            "issues": [],
        }
        result = build_judge_feedback_hint(scores)
        assert "AI-tone" in result
        assert "3/10" in result

    def test_judge_feedback_hint_handles_narrative(self):
        scores = {
            "hook_strength": 8,
            "spoiler_level": 3,
            "plot_accuracy": 9,
            "anti_ai_compliance": 8,
            "narrative_adherence": 3,
            "verdict": "retry",
            "issues": [],
        }
        result = build_judge_feedback_hint(scores)
        assert "Narrative principles" in result
        assert "3/10" in result

    def test_judge_feedback_hint_no_feedback_when_all_pass(self):
        scores = {
            "hook_strength": 8,
            "spoiler_level": 3,
            "plot_accuracy": 9,
            "anti_ai_compliance": 8,
            "narrative_adherence": 7,
            "verdict": "pass",
            "issues": [],
        }
        assert build_judge_feedback_hint(scores) == ""

    def test_judge_script_ci_mode_returns_five_dimensions(self):
        """CI mode should return default pass score with all 5 dimensions."""
        from movie_narrator.pipeline.script import judge_script

        with patch("movie_narrator.pipeline.script.is_ci", return_value=True):
            scores = judge_script([], "test", None)
        assert "anti_ai_compliance" in scores
        assert "narrative_adherence" in scores
        assert scores["verdict"] == "pass"


# ── 4. Beat deduplication ─────────────────────────────────


class TestBeatDeduplication:
    def test_no_duplicates_unchanged(self):
        beats = ["主角出场", "反派登场", "最终决战"]
        result, meta = _deduplicate_beats(beats)
        assert result == beats

    def test_exact_duplicates_removed(self):
        beats = ["主角出场", "主角出场", "最终决战"]
        result, _ = _deduplicate_beats(beats)
        assert len(result) == 2
        assert result[0] == "主角出场"
        assert result[1] == "最终决战"

    def test_near_duplicates_removed(self):
        beats = [
            "主角发现了一个惊天秘密",
            "主角发现了一个惊天秘密。",
            "反派开始行动",
        ]
        result, _ = _deduplicate_beats(beats)
        assert len(result) == 2

    def test_meta_preserved_for_kept_beats(self):
        beats = ["主角出场", "主角出场", "最终决战"]
        meta = [
            {"text": "主角出场", "act": 1},
            {"text": "主角出场", "act": 1},
            {"text": "最终决战", "act": 2},
        ]
        result, result_meta = _deduplicate_beats(beats, meta)
        assert len(result) == 2
        assert len(result_meta) == 2
        assert result_meta[0]["text"] == "主角出场"
        assert result_meta[1]["text"] == "最终决战"

    def test_single_beat_unchanged(self):
        beats = ["only one"]
        result, _ = _deduplicate_beats(beats)
        assert result == beats

    def test_empty_list_unchanged(self):
        result, _ = _deduplicate_beats([])
        assert result == []

    def test_char_bigrams_short_text(self):
        bg = _char_bigrams("a")
        assert bg == frozenset(["a"])

    def test_char_bigrams_normal_text(self):
        bg = _char_bigrams("abc")
        assert bg == frozenset(["ab", "bc"])

    def test_jaccard_identical(self):
        bg = _char_bigrams("same text")
        assert _jaccard_similarity(bg, bg) == 1.0

    def test_jaccard_disjoint(self):
        a = frozenset(["ab", "cd"])
        b = frozenset(["ef", "gh"])
        assert _jaccard_similarity(a, b) == 0.0


# ── 5. Built-in hook template library ─────────────────────


class TestHookTemplateLibrary:
    def test_default_templates_exist(self):
        assert len(DEFAULT_HOOK_TEMPLATES) > 0
        for tmpl in DEFAULT_HOOK_TEMPLATES:
            assert "{movie}" in tmpl

    def test_build_hook_hint_falls_back_to_defaults(self):
        """When hook_templates is None, use DEFAULT_HOOK_TEMPLATES."""
        result = build_hook_hint(None, "测试电影")
        assert "测试电影" in result
        assert "scroll-stopping" in result

    def test_build_hook_hint_uses_custom_when_provided(self):
        custom = ["{movie}太炸了"]
        result = build_hook_hint(custom, "我的电影")
        assert "我的电影太炸了" in result
        # Should NOT contain default templates
        assert "99%的人都没看懂" not in result

    def test_build_hook_hint_fills_movie_placeholder(self):
        result = build_hook_hint(None, "盗梦空间")
        assert "盗梦空间" in result
        # Template lines should have the movie name filled in
        # (the instruction line intentionally keeps {movie} as a placeholder hint)
        assert "盗梦空间里这个细节" in result


# ── 6. Script-level QA gate ───────────────────────────────


class TestScriptQaGate:
    def _make_ctx(self, tmp_path):
        return Context(
            movie_name="test",
            style="test",
            duration=60,
            output_dir=str(tmp_path),
            services=Services(console=MagicMock()),
        )

    def test_no_issues_for_good_script(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        segments = [
            ScriptSegment(text="这是一段足够长的旁白文案"),
            ScriptSegment(text="另一段完全不同的叙述内容"),
            ScriptSegment(text="第三段独特的电影解说文字"),
        ]
        issues = validate_script_quality(segments, 3, 15, ctx)
        assert issues == []
        assert ctx.metadata["script_qa"]["total_issues"] == 0

    def test_detects_too_short_segment(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        segments = [
            ScriptSegment(text="足够长的开头旁白"),
            ScriptSegment(text="x"),  # too short
            ScriptSegment(text="第三段独特的电影解说"),
        ]
        issues = validate_script_quality(segments, 3, 15, ctx)
        assert any("too short" in i for i in issues)

    def test_detects_too_long_segment(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        long_text = "这是一段非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的旁白"
        segments = [
            ScriptSegment(text="足够长的开头旁白"),
            ScriptSegment(text="另一段独特的叙述"),
            ScriptSegment(text=long_text),
        ]
        issues = validate_script_quality(segments, 3, 15, ctx)
        assert any("exceeds length" in i for i in issues)

    def test_detects_duplicate_segments(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        segments = [
            ScriptSegment(text="主角发现了惊天秘密"),
            ScriptSegment(text="主角发现了惊天秘密"),  # duplicate
            ScriptSegment(text="另一段完全不同的叙述"),
        ]
        issues = validate_script_quality(segments, 3, 15, ctx)
        assert any("near-duplicate" in i for i in issues)

    def test_detects_short_hook(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        segments = [
            ScriptSegment(text="ab"),  # hook too short (< 4 chars)
            ScriptSegment(text="另一段完全不同的叙述内容"),
            ScriptSegment(text="第三段独特的电影解说文字"),
        ]
        issues = validate_script_quality(segments, 3, 15, ctx)
        assert any("hook" in i.lower() for i in issues)

    def test_stores_qa_metadata(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        segments = [
            ScriptSegment(text="足够长的开头旁白文案"),
            ScriptSegment(text="另一段完全不同的叙述"),
        ]
        validate_script_quality(segments, 2, 15, ctx)
        assert "script_qa" in ctx.metadata
        qa = ctx.metadata["script_qa"]
        assert "total_issues" in qa
        assert "too_short" in qa
        assert "too_long" in qa
        assert "duplicates" in qa
        assert "issues" in qa

    def test_empty_segments_no_crash(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        issues = validate_script_quality([], 0, 15, ctx)
        assert isinstance(issues, list)
