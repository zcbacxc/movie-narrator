# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Subtitle quality validation — CPS, overlap detection, line length, display fit.

v0.5.10: Per-cue subtitle quality metrics for the subtitle pipeline.
All functions accept timed segments and return structured metrics.
They are advisory (soft gates) — issues are logged as warnings and
stored in ``ctx.metadata["subtitle_qa"]`` for diagnostics, but never
block the pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..models import TimedSegment


# ── Thresholds ────────────────────────────────────────────

# CPS (characters per second) — industry standards:
#   - Netflix: ≤ 20 CPS for English, ≤ 12 CPS for CJK
#   - We use a moderate threshold that works for mixed content.
#   CJK text is detected by the presence of CJK Unicode blocks.
_MAX_CPS_LATIN = 20.0
_MAX_CPS_CJK = 15.0

# Line length (characters per displayed line)
_MAX_CHARS_PER_LINE_LATIN = 42  # Netflix guideline for Latin scripts
_MAX_CHARS_PER_LINE_CJK = 18    # Common CJK guideline

# Minimum display duration (seconds) — Netflix: 0.5s minimum
_MIN_DURATION_S = 0.5

# Maximum gap between cues (seconds) — large gaps may indicate missing content
_MAX_GAP_S = 5.0


# ── CJK detection ─────────────────────────────────────────

_CJK_RANGES = [
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x20000, 0x2A6DF),  # CJK Extension B
    (0x3040, 0x309F),    # Hiragana
    (0x30A0, 0x30FF),    # Katakana
    (0xAC00, 0xD7AF),    # Hangul Syllables
]


def _is_cjk_char(ch: str) -> bool:
    """Check if a character is in a CJK Unicode block."""
    cp = ord(ch)
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def _is_cjk_text(text: str) -> bool:
    """True if >30% of non-space characters are CJK."""
    if not text:
        return False
    total = sum(1 for c in text if not c.isspace())
    if total == 0:
        return False
    cjk = sum(1 for c in text if _is_cjk_char(c))
    return (cjk / total) > 0.3


def _effective_length(text: str) -> int:
    """Effective character count for CPS calculation.

    CJK characters count as 2 (they take roughly twice the reading time
    of Latin characters).  Spaces and punctuation are counted as 1.
    """
    return sum(2 if _is_cjk_char(c) else 1 for c in text if not c.isspace())


# ── Metrics dataclass ─────────────────────────────────────


@dataclass
class SubtitleCueMetrics:
    """Quality metrics for a single subtitle cue."""

    index: int
    text: str
    start: float
    end: float
    duration_s: float
    cps: float  # characters per second (effective)
    char_count: int  # raw character count (non-space)
    eff_char_count: int  # effective character count (CJK weighted)
    is_cjk: bool
    line_count_estimate: int  # estimated displayed lines
    max_line_chars: int  # max chars per line for this script
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration_s": round(self.duration_s, 3),
            "cps": round(self.cps, 1),
            "char_count": self.char_count,
            "eff_char_count": self.eff_char_count,
            "is_cjk": self.is_cjk,
            "line_count_estimate": self.line_count_estimate,
            "max_line_chars": self.max_line_chars,
            "issues": self.issues,
        }


@dataclass
class SubtitleOverlap:
    """Detected overlap between two cues."""

    index_a: int
    index_b: int
    overlap_s: float

    def to_dict(self) -> dict:
        return {
            "index_a": self.index_a,
            "index_b": self.index_b,
            "overlap_s": round(self.overlap_s, 3),
        }


# ── Core checks ───────────────────────────────────────────


def check_cps(
    text: str,
    duration_s: float,
    *,
    max_cps_latin: float = _MAX_CPS_LATIN,
    max_cps_cjk: float = _MAX_CPS_CJK,
) -> tuple[float, bool]:
    """Calculate characters per second and check against threshold.

    Returns ``(cps, is_high)`` where ``is_high`` is True when CPS
    exceeds the script-appropriate threshold.
    """
    if duration_s <= 0:
        return 0.0, False
    eff_len = _effective_length(text)
    cps = eff_len / duration_s
    threshold = max_cps_cjk if _is_cjk_text(text) else max_cps_latin
    return cps, cps > threshold


def check_line_length(
    text: str,
    *,
    max_chars_latin: int = _MAX_CHARS_PER_LINE_LATIN,
    max_chars_cjk: int = _MAX_CHARS_PER_LINE_CJK,
) -> tuple[int, bool]:
    """Estimate displayed line count and check against max chars per line.

    Returns ``(estimated_lines, is_too_long)`` where ``is_too_long``
    is True when any line exceeds the character limit.
    """
    is_cjk = _is_cjk_text(text)
    max_chars = max_chars_cjk if is_cjk else max_chars_latin

    # Split on explicit newlines first (bilingual or pre-formatted)
    lines = text.split("\n")
    total_lines = 0
    too_long = False
    for line in lines:
        if is_cjk:
            # CJK: each line is max_chars wide
            n = max(1, (len(line) + max_chars - 1) // max_chars) if line else 1
            total_lines += n
            if len(line) > max_chars:
                too_long = True
        else:
            # Latin: estimate by word count
            words = line.split()
            n = 1
            cur_len = 0
            for w in words:
                wlen = len(w)
                # A single word longer than max_chars will wrap
                if wlen > max_chars:
                    word_lines = (wlen + max_chars - 1) // max_chars
                    if cur_len > 0:
                        n += 1
                        cur_len = 0
                    n += word_lines - 1
                    cur_len = wlen % max_chars or max_chars
                    too_long = True
                elif cur_len + wlen + (1 if cur_len > 0 else 0) > max_chars:
                    n += 1
                    cur_len = wlen
                else:
                    cur_len += wlen + (1 if cur_len > 0 else 0)
            total_lines += n
            if len(line) > max_chars:
                too_long = True

    return total_lines, too_long


def check_overlaps(
    segments: list[TimedSegment],
) -> list[SubtitleOverlap]:
    """Detect overlapping cues in a list of timed segments.

    Returns a list of :class:`SubtitleOverlap` instances.  An overlap
    occurs when ``segments[i].end > segments[i+1].start``.
    """
    overlaps: list[SubtitleOverlap] = []
    for i in range(len(segments) - 1):
        cur = segments[i]
        nxt = segments[i + 1]
        if cur.end > nxt.start:
            overlaps.append(SubtitleOverlap(
                index_a=i,
                index_b=i + 1,
                overlap_s=round(cur.end - nxt.start, 3),
            ))
    return overlaps


def check_display_fit(
    text: str,
    *,
    video_width: int = 1920,
    max_width_ratio: float = 0.9,
    max_lines: int = 2,
    fontsize: int = 100,
    is_vertical: bool = False,
) -> tuple[bool, int]:
    """Estimate whether translated text will fit the render area.

    Uses a heuristic character-width model (not pixel-precise, but
    good enough for flagging potential overflow before render).

    Returns ``(fits, estimated_lines)``.
    """
    if is_vertical:
        max_width_ratio = min(max_width_ratio, 0.82)
        max_lines = max(max_lines, 2)

    usable_width = int(video_width * max_width_ratio)
    is_cjk = _is_cjk_text(text)

    # Estimate average character width in pixels
    # CJK: ~fontsize * 1.0, Latin: ~fontsize * 0.55
    char_width = fontsize if is_cjk else fontsize * 0.55
    max_chars_per_line = max(1, int(usable_width / char_width))

    # Count how many lines the text will need
    explicit_lines = text.split("\n")
    total_lines = 0
    for line in explicit_lines:
        if not line:
            total_lines += 1
            continue
        if is_cjk:
            total_lines += max(1, (len(line) + max_chars_per_line - 1) // max_chars_per_line)
        else:
            words = line.split()
            if not words and line:
                # Non-empty line with no spaces — treat as single word
                words = [line]
            n = 1
            cur_len = 0
            for w in words:
                wlen = len(w)
                # A single word longer than max_chars_per_line wraps
                if wlen > max_chars_per_line:
                    word_lines = (wlen + max_chars_per_line - 1) // max_chars_per_line
                    if cur_len > 0:
                        n += 1
                        cur_len = 0
                    n += word_lines - 1
                    cur_len = wlen % max_chars_per_line or max_chars_per_line
                elif cur_len + wlen + (1 if cur_len > 0 else 0) > max_chars_per_line:
                    n += 1
                    cur_len = wlen
                else:
                    cur_len += wlen + (1 if cur_len > 0 else 0)
            total_lines += n

    fits = total_lines <= max_lines
    return fits, total_lines


# ── Per-cue analysis ──────────────────────────────────────


def analyze_cue(
    seg: TimedSegment,
    index: int,
    translated_text: Optional[str] = None,
) -> SubtitleCueMetrics:
    """Run all quality checks on a single subtitle cue.

    If ``translated_text`` is provided, checks the translated text
    instead of the original (for translation quality validation).
    """
    text = translated_text if translated_text is not None else seg.text
    duration_s = seg.end - seg.start
    is_cjk = _is_cjk_text(text)
    char_count = sum(1 for c in text if not c.isspace())
    eff_char_count = _effective_length(text)

    cps, cps_high = check_cps(text, duration_s)
    line_count, line_too_long = check_line_length(text)

    max_line_chars = _MAX_CHARS_PER_LINE_CJK if is_cjk else _MAX_CHARS_PER_LINE_LATIN

    issues: list[str] = []

    if cps_high:
        threshold = _MAX_CPS_CJK if is_cjk else _MAX_CPS_LATIN
        issues.append(
            f"high CPS: {cps:.1f} > {threshold:.0f} (text may be hard to read)"
        )

    if line_too_long:
        issues.append(
            f"line too long: exceeds {max_line_chars} chars/line"
        )

    if duration_s < _MIN_DURATION_S:
        issues.append(
            f"duration too short: {duration_s:.3f}s < {_MIN_DURATION_S}s"
        )

    if char_count == 0:
        issues.append("empty text (no non-space characters)")

    return SubtitleCueMetrics(
        index=index,
        text=text,
        start=seg.start,
        end=seg.end,
        duration_s=round(duration_s, 3),
        cps=round(cps, 1),
        char_count=char_count,
        eff_char_count=eff_char_count,
        is_cjk=is_cjk,
        line_count_estimate=line_count,
        max_line_chars=max_line_chars,
        issues=issues,
    )


def validate_subtitles(
    segments: list[TimedSegment],
    translated_texts: Optional[list[str]] = None,
) -> dict:
    """Full subtitle validation: per-cue metrics + overlap detection.

    Returns a dict suitable for ``ctx.metadata["subtitle_qa"]``.
    """
    use_translated = (
        translated_texts is not None
        and len(translated_texts) == len(segments)
    )

    cue_metrics: list[SubtitleCueMetrics] = []
    for i, seg in enumerate(segments):
        tr = translated_texts[i] if use_translated else None
        cue_metrics.append(analyze_cue(seg, i, tr))

    overlaps = check_overlaps(segments)

    # Check for large gaps
    gaps: list[dict] = []
    for i in range(len(segments) - 1):
        gap = segments[i + 1].start - segments[i].end
        if gap > _MAX_GAP_S:
            gaps.append({
                "index_a": i,
                "index_b": i + 1,
                "gap_s": round(gap, 3),
            })

    return aggregate_cue_metrics(cue_metrics, overlaps, gaps, use_translated)


def aggregate_cue_metrics(
    metrics: list[SubtitleCueMetrics],
    overlaps: list[SubtitleOverlap],
    gaps: list[dict],
    is_translated: bool = False,
) -> dict:
    """Aggregate per-cue metrics into a summary dict for metadata.json."""
    total_issues = sum(len(m.issues) for m in metrics)
    cues_with_issues = sum(1 for m in metrics if m.issues)
    avg_cps = (
        sum(m.cps for m in metrics if m.cps > 0)
        / max(1, sum(1 for m in metrics if m.cps > 0))
    )
    max_cps = max((m.cps for m in metrics), default=0.0)
    avg_duration = sum(m.duration_s for m in metrics) / max(1, len(metrics))

    return {
        "track": "translated" if is_translated else "original",
        "cue_count": len(metrics),
        "avg_cps": round(avg_cps, 1),
        "max_cps": round(max_cps, 1),
        "avg_duration_s": round(avg_duration, 3),
        "cues_with_issues": cues_with_issues,
        "total_issues": total_issues,
        "overlaps": [o.to_dict() for o in overlaps],
        "overlap_count": len(overlaps),
        "gaps": gaps,
        "gap_count": len(gaps),
        "all_issues": [
            {"index": m.index, "issues": m.issues}
            for m in metrics if m.issues
        ],
        "cues": [m.to_dict() for m in metrics],
    }
