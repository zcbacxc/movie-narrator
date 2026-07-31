"""Tests for judge feedback loop.

Verifies that:
1. build_judge_feedback_hint returns "" on first attempt (None scores)
2. build_judge_feedback_hint returns "" when verdict was pass
3. build_judge_feedback_hint returns targeted feedback on low hook score
4. build_judge_feedback_hint returns targeted feedback on high spoiler
5. build_judge_feedback_hint returns targeted feedback on low accuracy
6. build_judge_feedback_hint includes raw issues from judge
7. EXPAND_PROMPT has {judge_feedback} placeholder
8. _expand_beats_to_script accepts prev_judge_scores parameter
"""

from movie_narrator.utils.prompts import (
    EXPAND_PROMPT,
    build_judge_feedback_hint,
)


class TestBuildJudgeFeedbackHint:
    def test_none_scores_returns_empty(self):
        assert build_judge_feedback_hint(None) == ""

    def test_empty_dict_returns_empty(self):
        assert build_judge_feedback_hint({}) == ""

    def test_pass_verdict_no_issues_returns_empty(self):
        scores = {
            "hook_strength": 8,
            "spoiler_level": 3,
            "plot_accuracy": 9,
            "verdict": "pass",
            "issues": [],
        }
        assert build_judge_feedback_hint(scores) == ""

    def test_low_hook_returns_targeted_feedback(self):
        scores = {
            "hook_strength": 3,
            "spoiler_level": 4,
            "plot_accuracy": 8,
            "verdict": "retry",
            "issues": [],
        }
        result = build_judge_feedback_hint(scores)
        assert "hook" in result.lower()
        assert "3/10" in result
        assert "IMPROVEMENT DIRECTIVE" in result

    def test_high_spoiler_returns_targeted_feedback(self):
        scores = {
            "hook_strength": 7,
            "spoiler_level": 9,
            "plot_accuracy": 8,
            "verdict": "retry",
            "issues": [],
        }
        result = build_judge_feedback_hint(scores)
        assert "spoiler" in result.lower()
        assert "9/10" in result

    def test_low_accuracy_returns_targeted_feedback(self):
        scores = {
            "hook_strength": 7,
            "spoiler_level": 4,
            "plot_accuracy": 3,
            "verdict": "retry",
            "issues": [],
        }
        result = build_judge_feedback_hint(scores)
        assert "accuracy" in result.lower()
        assert "3/10" in result

    def test_raw_issues_included(self):
        scores = {
            "hook_strength": 8,
            "spoiler_level": 3,
            "plot_accuracy": 9,
            "verdict": "retry",
            "issues": ["The ending is too predictable"],
        }
        result = build_judge_feedback_hint(scores)
        assert "ending is too predictable" in result

    def test_duplicate_issues_not_repeated(self):
        scores = {
            "hook_strength": 3,
            "spoiler_level": 4,
            "plot_accuracy": 8,
            "verdict": "retry",
            "issues": ["The opening hook was too weak"],
        }
        result = build_judge_feedback_hint(scores)
        # The issue text should not be duplicated since the hook score
        # already generated a targeted feedback line.
        assert result.count("hook") <= 3  # appears in directive + score line

    def test_all_three_problems(self):
        scores = {
            "hook_strength": 2,
            "spoiler_level": 9,
            "plot_accuracy": 2,
            "verdict": "retry",
            "issues": [],
        }
        result = build_judge_feedback_hint(scores)
        assert "hook" in result.lower()
        assert "spoiler" in result.lower()
        assert "accuracy" in result.lower()


class TestExpandPromptHasPlaceholder:
    def test_judge_feedback_placeholder_exists(self):
        assert "{judge_feedback}" in EXPAND_PROMPT


class TestExpandBeatsAcceptsPrevScores:
    def test_function_accepts_prev_judge_scores_kwarg(self):
        """Verify _expand_beats_to_script has prev_judge_scores parameter."""
        import inspect
        from movie_narrator.pipeline.script import _expand_beats_to_script
        sig = inspect.signature(_expand_beats_to_script)
        assert "prev_judge_scores" in sig.parameters
        assert sig.parameters["prev_judge_scores"].default is None
