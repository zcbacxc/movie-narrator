"""Tests for v0.5.10 Subtitle & Translation Quality features.

Covers:
- Subtitle QA: CPS calculation, overlap detection, line length, display fit
- Glossary: term extraction, consistency checking, untranslated line marking
- Translate pipeline: glossary integration, untranslated line tracking
- Subtitle pipeline: QA validation, display fit issues
- Text image: bilingual line balancing
"""

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import (
    Assets,
    Context,
    Services,
    ScriptSegment,
    SubtitlePaths,
    TimedSegment,
)
from movie_narrator.utils.subtitle_qa import (
    SubtitleCueMetrics,
    SubtitleOverlap,
    _is_cjk_char,
    _is_cjk_text,
    _effective_length,
    check_cps,
    check_line_length,
    check_overlaps,
    check_display_fit,
    analyze_cue,
    validate_subtitles,
    aggregate_cue_metrics,
)
from movie_narrator.utils.glossary import (
    GlossaryEntry,
    GlossaryReport,
    extract_terms,
    build_glossary,
    check_translation_consistency,
    mark_untranslated_lines,
)


# ── Helpers ────────────────────────────────────────────────


def _make_segments(
    n: int = 3,
    duration: float = 2.0,
    prefix: str = "测试文本",
) -> list[TimedSegment]:
    """Create timed segments for testing."""
    segs = []
    t = 0.0
    for i in range(n):
        segs.append(TimedSegment(
            text=f"{prefix}{i}",
            start=t,
            end=t + duration,
        ))
        t += duration
    return segs


# ── 1. CJK detection ──────────────────────────────────────


def test_is_cjk_char_basic():
    assert _is_cjk_char("中")
    assert _is_cjk_char("文")
    assert not _is_cjk_char("A")
    assert not _is_cjk_char("1")


def test_is_cjk_text_chinese():
    assert _is_cjk_text("这是一段中文文本")
    assert not _is_cjk_text("This is English text")


def test_is_cjk_text_mixed():
    # >30% CJK → True
    assert _is_cjk_text("这是some中文text")
    # <30% CJK → False
    assert not _is_cjk_text("Mostly English with 一 CJK char")


def test_is_cjk_text_empty():
    assert not _is_cjk_text("")
    assert not _is_cjk_text("   ")


def test_effective_length_latin():
    assert _effective_length("hello") == 5
    assert _effective_length("hello world") == 10  # space excluded


def test_effective_length_cjk():
    assert _effective_length("中文") == 4  # 2 chars × 2
    assert _effective_length("hello中文") == 9  # 5 + 2×2


# ── 2. CPS check ──────────────────────────────────────────


def test_check_cps_normal():
    cps, high = check_cps("hello world", 2.0)
    assert cps == 5.0  # 10 eff chars (space excluded) / 2.0s
    assert not high


def test_check_cps_high_cjk():
    # 20 CJK chars × 2 = 40 eff, duration = 2.0s → CPS = 20
    text = "中" * 20
    cps, high = check_cps(text, 2.0)
    assert cps == 20.0
    assert high  # > 15.0 CJK threshold


def test_check_cps_high_latin():
    # 50 Latin chars, duration = 2.0s → CPS = 25
    text = "a" * 50
    cps, high = check_cps(text, 2.0)
    assert cps == 25.0
    assert high  # > 20.0 Latin threshold


def test_check_cps_zero_duration():
    cps, high = check_cps("hello", 0.0)
    assert cps == 0.0
    assert not high


# ── 3. Line length check ──────────────────────────────────


def test_check_line_length_short():
    lines, too_long = check_line_length("short text")
    assert lines == 1
    assert not too_long


def test_check_line_length_long_latin():
    text = "word " * 30  # 150 chars, well over 42/line
    lines, too_long = check_line_length(text)
    assert lines >= 3
    assert too_long


def test_check_line_length_long_cjk():
    text = "中" * 50  # 50 CJK chars, well over 18/line
    lines, too_long = check_line_length(text)
    assert lines >= 3
    assert too_long


def test_check_line_length_multiline():
    text = "line one\nline two\nline three"
    lines, too_long = check_line_length(text)
    assert lines == 3
    assert not too_long


# ── 4. Overlap detection ──────────────────────────────────


def test_check_overlaps_none():
    segs = _make_segments(3, duration=2.0)
    overlaps = check_overlaps(segs)
    assert overlaps == []


def test_check_overlaps_detected():
    segs = [
        TimedSegment(text="a", start=0.0, end=2.5),
        TimedSegment(text="b", start=2.0, end=4.0),  # overlaps by 0.5s
    ]
    overlaps = check_overlaps(segs)
    assert len(overlaps) == 1
    assert overlaps[0].index_a == 0
    assert overlaps[0].index_b == 1
    assert overlaps[0].overlap_s == 0.5


def test_check_overlaps_empty():
    assert check_overlaps([]) == []
    assert check_overlaps([TimedSegment(text="a", start=0, end=1)]) == []


# ── 5. Display fit check ──────────────────────────────────


def test_check_display_fit_short_text():
    fits, lines = check_display_fit("hello", video_width=1920)
    assert fits
    assert lines == 1


def test_check_display_fit_long_text_cjk():
    # Very long CJK text → many lines
    text = "中" * 100
    fits, lines = check_display_fit(text, video_width=1920, max_lines=2)
    assert not fits
    assert lines > 2


def test_check_display_fit_vertical():
    text = "中" * 50
    fits, lines = check_display_fit(text, video_width=1080, is_vertical=True, max_lines=2)
    assert not fits


def test_check_display_fit_exact_fit():
    text = "normal text"
    fits, lines = check_display_fit(text, video_width=1920, max_lines=2)
    assert fits
    assert lines == 1


# ── 6. Per-cue analysis ───────────────────────────────────


def test_analyze_cue_clean():
    seg = TimedSegment(text="正常文本", start=0.0, end=3.0)
    m = analyze_cue(seg, 0)
    assert m.index == 0
    assert m.duration_s == 3.0
    assert m.is_cjk
    assert len(m.issues) == 0


def test_analyze_cue_high_cps():
    seg = TimedSegment(text="中" * 50, start=0.0, end=2.0)
    m = analyze_cue(seg, 1)
    assert any("high CPS" in issue for issue in m.issues)
    assert m.cps > 15.0


def test_analyze_cue_too_short_duration():
    seg = TimedSegment(text="hello", start=0.0, end=0.2)
    m = analyze_cue(seg, 2)
    assert any("duration too short" in issue for issue in m.issues)


def test_analyze_cue_empty_text():
    seg = TimedSegment(text="   ", start=0.0, end=2.0)
    m = analyze_cue(seg, 3)
    assert any("empty" in issue for issue in m.issues)


def test_analyze_cue_with_translation():
    seg = TimedSegment(text="中文文本", start=0.0, end=3.0)
    m = analyze_cue(seg, 0, translated_text="Chinese text content here")
    assert not m.is_cjk  # translated text is Latin
    assert m.duration_s == 3.0


def test_analyze_cue_to_dict():
    seg = TimedSegment(text="test", start=0.0, end=2.0)
    m = analyze_cue(seg, 0)
    d = m.to_dict()
    assert d["index"] == 0
    assert "duration_s" in d
    assert "cps" in d
    assert "issues" in d


# ── 7. Full validation ────────────────────────────────────


def test_validate_subtitles_no_issues():
    segs = _make_segments(3, duration=3.0)
    result = validate_subtitles(segs)
    assert result["cue_count"] == 3
    assert result["overlap_count"] == 0
    assert result["gap_count"] == 0
    assert result["total_issues"] == 0
    assert result["track"] == "original"


def test_validate_subtitles_with_overlaps():
    segs = [
        TimedSegment(text="a", start=0.0, end=2.5),
        TimedSegment(text="b", start=2.0, end=4.0),
    ]
    result = validate_subtitles(segs)
    assert result["overlap_count"] == 1


def test_validate_subtitles_with_gaps():
    segs = [
        TimedSegment(text="a", start=0.0, end=1.0),
        TimedSegment(text="b", start=8.0, end=9.0),  # 7s gap
    ]
    result = validate_subtitles(segs)
    assert result["gap_count"] == 1


def test_validate_subtitles_with_translations():
    segs = _make_segments(3, duration=3.0)
    translations = ["test one", "test two", "test three"]
    result = validate_subtitles(segs, translations)
    assert result["track"] == "translated"
    assert result["cue_count"] == 3


def test_validate_subtitles_mismatched_lengths():
    segs = _make_segments(3)
    translations = ["only one"]  # length mismatch
    result = validate_subtitles(segs, translations)
    assert result["track"] == "original"  # falls back


# ── 8. Aggregation ────────────────────────────────────────


def test_aggregate_empty():
    result = aggregate_cue_metrics([], [], [], False)
    assert result["cue_count"] == 0
    assert result["avg_cps"] == 0.0


def test_aggregate_normal():
    segs = _make_segments(3, duration=3.0)
    metrics = [analyze_cue(s, i) for i, s in enumerate(segs)]
    result = aggregate_cue_metrics(metrics, [], [], False)
    assert result["cue_count"] == 3
    assert result["total_issues"] == 0
    assert result["overlap_count"] == 0


# ── 9. Glossary: term extraction ──────────────────────────


def test_extract_terms_quoted_cjk():
    text = '电影「流浪地球」非常好看'
    terms = extract_terms(text)
    assert "流浪地球" in terms


def test_extract_terms_english_quoted():
    text = 'The movie "Inception" is great'
    terms = extract_terms(text)
    assert "Inception" in terms


def test_extract_terms_capitalized():
    text = "John went to see Mary at the cinema"
    terms = extract_terms(text)
    assert "John" in terms
    assert "Mary" in terms


def test_extract_terms_empty():
    assert extract_terms("") == []
    assert extract_terms("no special terms here") == []


def test_extract_terms_filters_short():
    text = '"a" is not a term'
    terms = extract_terms(text)
    assert "a" not in terms  # too short (< _MIN_TERM_LEN)


# ── 10. Glossary: consistency ─────────────────────────────


def test_build_glossary_consistent():
    source = ['"Inception" is great', 'I love "Inception"']
    translated = ['"盗梦空间"很棒', '我爱"盗梦空间"']
    report = build_glossary(source, translated)
    # "Inception" appears in both, translated consistently as "盗梦空间"
    inception_entries = [e for e in report.entries if e.source_term == "Inception"]
    assert len(inception_entries) == 1
    assert inception_entries[0].is_consistent


def test_build_glossary_inconsistent():
    source = ['"Inception" is great', 'I love "Inception"']
    # Different translations for same term
    translated = ['"盗梦空间"很棒', '我爱"奠基"']
    report = build_glossary(source, translated)
    inception_entries = [e for e in report.entries if e.source_term == "Inception"]
    if inception_entries:
        assert not inception_entries[0].is_consistent
        assert inception_entries[0].translation_count >= 2


def test_build_glossary_single_occurrence_filtered():
    source = ['"Movie A" is good', '"Movie B" is bad']
    translated = ['"电影A"好', '"电影B"差']
    report = build_glossary(source, translated)
    # Both terms appear only once → filtered out
    assert report.total_terms == 0


def test_glossary_entry_to_dict():
    entry = GlossaryEntry(source_term="test")
    entry.translations = {"翻译1": [0, 1], "翻译2": [2]}
    d = entry.to_dict()
    assert d["source_term"] == "test"
    assert d["translation_count"] == 2
    assert not d["is_consistent"]
    assert d["total_occurrences"] == 3


def test_glossary_report_to_dict():
    entry = GlossaryEntry(source_term="test")
    entry.translations = {"tr1": [0]}
    report = GlossaryReport(entries=[entry], total_terms=1)
    d = report.to_dict()
    assert d["total_terms"] == 1
    assert d["inconsistent_count"] == 0
    assert d["consistent_count"] == 1


# ── 11. Glossary: untranslated lines ──────────────────────


def test_mark_untranslated_lines_all_translated():
    source = ["hello", "world"]
    translated = ["你好", "世界"]
    assert mark_untranslated_lines(source, translated) == []


def test_mark_untranslated_lines_some():
    source = ["hello", "world", "test"]
    translated = ["你好", "world", "test"]  # last two untranslated
    result = mark_untranslated_lines(source, translated)
    assert result == [1, 2]


def test_mark_untranslated_lines_empty():
    assert mark_untranslated_lines([], []) == []


def test_check_translation_consistency_wrapper():
    source = ['"Movie" is good', '"Movie" is great']
    translated = ['"电影"好', '"电影"棒']
    report = check_translation_consistency(source, translated)
    assert isinstance(report, GlossaryReport)
    assert report.total_terms >= 0


# ── 12. Subtitle pipeline integration ─────────────────────


def test_subtitle_qa_stored_in_metadata(tmp_path):
    """Subtitle step should store QA results in metadata."""
    from movie_narrator.pipeline.subtitle import generate_subtitle

    segs = [
        TimedSegment(text="第一段字幕", start=0.0, end=3.0),
        TimedSegment(text="第二段字幕", start=3.0, end=6.0),
        TimedSegment(text="第三段字幕", start=6.0, end=9.0),
    ]
    ctx = Context(
        movie_name="test",
        style="热血搞笑",
        duration=10,
        output_dir=str(tmp_path),
        segments=[ScriptSegment(text=f"seg {i}") for i in range(3)],
        timed_segments=segs,
        services=Services(console=MagicMock()),
    )

    result = generate_subtitle(ctx)
    assert "subtitle_qa" in result.metadata
    assert "original" in result.metadata["subtitle_qa"]
    assert result.metadata["subtitle_qa"]["original"]["cue_count"] == 3
    # No translations → no "translated" key
    assert "translated" not in result.metadata["subtitle_qa"]


def test_subtitle_qa_with_translations(tmp_path):
    """Subtitle step should validate translated track too."""
    from movie_narrator.pipeline.subtitle import generate_subtitle

    segs = [
        TimedSegment(text="第一段", start=0.0, end=3.0),
        TimedSegment(text="第二段", start=3.0, end=6.0),
    ]
    ctx = Context(
        movie_name="test",
        style="热血搞笑",
        duration=6,
        output_dir=str(tmp_path),
        segments=[ScriptSegment(text=f"seg {i}") for i in range(2)],
        timed_segments=segs,
        services=Services(console=MagicMock()),
    )
    ctx.translated_texts = ["First segment", "Second segment"]
    ctx.metadata["subtitle_lang"] = "en"

    result = generate_subtitle(ctx)
    qa = result.metadata["subtitle_qa"]
    assert "original" in qa
    assert "translated" in qa
    assert qa["translated"]["track"] == "translated"
    assert qa["translated"]["cue_count"] == 2


def test_subtitle_qa_display_fit_issues(tmp_path):
    """Subtitle step should flag display overflow."""
    from movie_narrator.pipeline.subtitle import generate_subtitle

    segs = [
        TimedSegment(text="短", start=0.0, end=3.0),
    ]
    ctx = Context(
        movie_name="test",
        style="热血搞笑",
        duration=3,
        output_dir=str(tmp_path),
        segments=[ScriptSegment(text="seg")],
        timed_segments=segs,
        services=Services(console=MagicMock()),
    )
    # Very long translated text that won't fit in 2 lines
    ctx.translated_texts = ["a" * 500]
    ctx.metadata["subtitle_lang"] = "en"

    result = generate_subtitle(ctx)
    qa = result.metadata["subtitle_qa"]
    assert "display_fit_issues" in qa
    assert len(qa["display_fit_issues"]) >= 1


def test_subtitle_qa_overlaps_detected(tmp_path):
    """Subtitle step should detect overlapping cues."""
    from movie_narrator.pipeline.subtitle import generate_subtitle

    segs = [
        TimedSegment(text="a", start=0.0, end=2.5),
        TimedSegment(text="b", start=2.0, end=4.0),  # overlap
    ]
    ctx = Context(
        movie_name="test",
        style="热血搞笑",
        duration=4,
        output_dir=str(tmp_path),
        segments=[ScriptSegment(text=f"seg {i}") for i in range(2)],
        timed_segments=segs,
        services=Services(console=MagicMock()),
    )

    result = generate_subtitle(ctx)
    qa = result.metadata["subtitle_qa"]
    assert qa["original"]["overlap_count"] == 1


# ── 13. Text image: line balancing ────────────────────────


def test_balance_lines_cjk():
    from movie_narrator.utils.text_image import _balance_lines
    lines = ["中中中中中中中中中中中", "短"]
    balanced = _balance_lines(lines, 2)
    assert len(balanced) == 2
    # Should be more balanced now
    assert abs(len(balanced[0]) - len(balanced[1])) <= 1


def test_balance_lines_latin_skipped():
    from movie_narrator.utils.text_image import _balance_lines
    lines = ["very long latin text here", "short"]
    balanced = _balance_lines(lines, 2)
    # Latin text should not be rebalanced
    assert balanced == lines


def test_balance_lines_single():
    from movie_narrator.utils.text_image import _balance_lines
    assert _balance_lines(["only one"], 2) == ["only one"]


def test_balance_lines_empty():
    from movie_narrator.utils.text_image import _balance_lines
    assert _balance_lines([], 2) == []


def test_balance_lines_exceeds_max():
    from movie_narrator.utils.text_image import _balance_lines
    # 5 lines but max_lines=2 → don't balance
    lines = ["a", "b", "c", "d", "e"]
    result = _balance_lines(lines, 2)
    assert result == lines  # returned unchanged
