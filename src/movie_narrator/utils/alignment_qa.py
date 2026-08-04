# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Alignment quality utilities — word-level remapping, confidence scoring, drift validation.

v0.5.11: Provides sub-segment precision alignment using WhisperX word-level
timestamps, per-segment confidence scoring, and alignment quality validation.

All functions are advisory (soft gates) — issues are logged as warnings
and stored in ``ctx.metadata["alignment_qa"]`` for diagnostics, but never
block the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import TimedSegment, WordSegment


# ── Thresholds ────────────────────────────────────────────

# Minimum word confidence to be considered "high quality"
_MIN_WORD_CONFIDENCE = 0.5

# Segment confidence below this threshold is flagged as "low confidence"
_LOW_CONFIDENCE_THRESHOLD = 0.6

# Minimum alignment drift ratio to trigger skip (v0.5.11: tightened from 0.5 to 0.3)
# When ASR returns a single segment for the entire audio and its duration
# differs from total narration duration by more than this ratio, alignment
# is deemed unreliable.
_DRIFT_THRESHOLD_V0511 = 0.3


# ── Data structures ───────────────────────────────────────


@dataclass
class AlignmentQualityMetrics:
    """Aggregated alignment quality metrics for the entire pipeline."""

    total_segments: int = 0
    segments_with_words: int = 0
    avg_confidence: float = 0.0
    min_confidence: float = 0.0
    max_confidence: float = 0.0
    low_confidence_count: int = 0
    low_confidence_indices: list[int] = field(default_factory=list)
    word_level_available: bool = False

    def to_dict(self) -> dict:
        """Convert the QA result to a dictionary.

        Returns:
            Dictionary representation of the QA result.
        """
        return {
            "total_segments": self.total_segments,
            "segments_with_words": self.segments_with_words,
            "avg_confidence": round(self.avg_confidence, 4),
            "min_confidence": round(self.min_confidence, 4),
            "max_confidence": round(self.max_confidence, 4),
            "low_confidence_count": self.low_confidence_count,
            "low_confidence_indices": self.low_confidence_indices,
            "word_level_available": self.word_level_available,
        }


# ── Word-level extraction ─────────────────────────────────


def extract_word_segments(
    whisperx_result: dict,
) -> list[dict]:
    """Extract word-level segments from a WhisperX align() result.

    WhisperX's ``align()`` returns a dict with a ``"word_segments"`` key
    containing per-word entries: ``{"word": "...", "start": ..., "end": ...,
    "score": ...}``.

    Returns:
        A list of word dicts with normalized keys.  Returns an empty
        list if no word-level data is available.
    """
    word_segments = whisperx_result.get("word_segments", [])
    if not word_segments:
        return []

    extracted: list[dict] = []
    for ws in word_segments:
        word = ws.get("word", "").strip()
        if not word:
            continue
        extracted.append({
            "word": word,
            "start": float(ws.get("start", 0.0)),
            "end": float(ws.get("end", 0.0)),
            "score": float(ws.get("score", 0.0)),
        })
    return extracted


def assign_words_to_segments(
    timed_segments: list[TimedSegment],
    word_segments: list[dict],
    wx_segments: list[dict],
) -> int:
    """Assign word-level timestamps to timed segments.

    Maps each ``TimedSegment`` to the WhisperX words that fall within its
    (remapped) time range.  Uses the segment-level ``wx_segments`` as
    fallback when word-level data is not available for a segment.

    Modifies ``timed_segments`` in-place: sets ``.words`` and ``.confidence``.

    Returns:
        The count of segments that received word-level data.
    """
    if not word_segments:
        return 0

    # Build a time-sorted index of words for efficient range queries.
    # Words are already in chronological order from WhisperX.
    word_ptr = 0
    n_words = len(word_segments)
    assigned_count = 0

    for ts in timed_segments:
        # Advance word_ptr to the first word that starts at or after ts.start
        while word_ptr < n_words and word_segments[word_ptr]["end"] < ts.start:
            word_ptr += 1

        # Collect words that fall within [ts.start, ts.end]
        ts_words: list[WordSegment] = []
        ptr = word_ptr
        while ptr < n_words and word_segments[ptr]["start"] <= ts.end:
            ws = word_segments[ptr]
            # Include word if it overlaps with the segment's time range
            if ws["end"] >= ts.start and ws["start"] <= ts.end:
                ts_words.append(WordSegment(
                    word=ws["word"],
                    start=ws["start"],
                    end=ws["end"],
                    score=ws["score"],
                ))
            ptr += 1

        if ts_words:
            ts.words = ts_words
            ts.confidence = compute_segment_confidence(ts_words)
            assigned_count += 1
        # If no words matched, leave words empty and confidence at 0.0

    return assigned_count


# ── Confidence scoring ────────────────────────────────────


def compute_segment_confidence(
    words: list[WordSegment],
) -> float:
    """Compute segment-level confidence from word-level scores.

    Returns:
        The average word confidence score (0.0–1.0).
        Returns 0.0 if the word list is empty.
    """
    if not words:
        return 0.0
    scores = [w.score for w in words if w.score > 0]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def flag_low_confidence_segments(
    timed_segments: list[TimedSegment],
    threshold: float = _LOW_CONFIDENCE_THRESHOLD,
) -> list[int]:
    """Identify segments with confidence below the threshold.

    Returns:
        A list of indices of low-confidence segments.
        Only checks segments that have word-level data (confidence > 0).
        Segments without word-level data are not flagged (they use TTS estimates).
    """
    indices: list[int] = []
    for i, ts in enumerate(timed_segments):
        if ts.confidence > 0 and ts.confidence < threshold:
            indices.append(i)
    return indices


# ── Word-level remapping ──────────────────────────────────


def word_level_remap(
    timed_segments: list[TimedSegment],
    word_segments: list[dict],
    prev_end: float = 0.0,
    min_duration: float = 0.1,
) -> int:
    """Remap segment boundaries using word-level timestamps.

    For each ``TimedSegment``, finds the first and last words whose
    timestamps overlap with the segment's current time range, then
    tightens the segment boundaries to match the actual word boundaries.

    This provides sub-segment precision compared to the segment-level
    remapping which uses the entire WhisperX segment's start/end.

    Enforces monotonic non-overlap: if the new start would be before
    ``prev_end``, it is clamped to ``prev_end``.

    Returns:
        The number of segments that were tightened (had their
        boundaries adjusted to word-level precision).
    """
    if not word_segments:
        return 0

    n_words = len(word_segments)
    word_ptr = 0
    tightened = 0
    current_prev_end = prev_end

    for ts in timed_segments:
        # Find the first word that ends at or after ts.start
        while word_ptr < n_words and word_segments[word_ptr]["end"] < ts.start:
            word_ptr += 1

        if word_ptr >= n_words:
            break

        # Find the last word that starts at or before ts.end
        end_ptr = word_ptr
        while end_ptr < n_words - 1 and word_segments[end_ptr + 1]["start"] <= ts.end:
            end_ptr += 1

        new_start = word_segments[word_ptr]["start"]
        new_end = word_segments[end_ptr]["end"]

        # Only tighten if the word-level range is narrower than current
        if new_start >= ts.start and new_end <= ts.end and new_end > new_start:
            # Enforce monotonic non-overlap
            if new_start < current_prev_end:
                new_start = current_prev_end
            if new_end <= new_start:
                new_end = new_start + min_duration

            if new_end > new_start:
                ts.start = new_start
                ts.end = new_end
                current_prev_end = new_end
                tightened += 1

    return tightened


# ── Drift detection ───────────────────────────────────────


def check_drift(
    timed_segments: list[TimedSegment],
    wx_segments: list[dict],
    threshold: float = _DRIFT_THRESHOLD_V0511,
) -> tuple[bool, float]:
    """Check if ASR drift is too large for reliable alignment.

    Returns:
        ``(is_drift_too_large, drift_ratio)``.
        Only triggers when ASR returns exactly 1 segment for the entire audio.
    """
    if len(wx_segments) != 1 or not timed_segments:
        return False, 0.0

    total_narr_duration = sum(ts.end - ts.start for ts in timed_segments)
    if total_narr_duration <= 0:
        return False, 0.0

    wx_duration = wx_segments[0]["end"] - wx_segments[0]["start"]
    drift_ratio = abs(wx_duration - total_narr_duration) / total_narr_duration
    return drift_ratio > threshold, drift_ratio


# ── Full validation ───────────────────────────────────────


def validate_alignment(
    timed_segments: list[TimedSegment],
) -> AlignmentQualityMetrics:
    """Run full alignment quality validation.

    Returns:
        An :class:`AlignmentQualityMetrics` summary suitable for
        ``ctx.metadata["alignment_qa"]``.
    """
    metrics = AlignmentQualityMetrics(
        total_segments=len(timed_segments),
    )

    confidences = [
        ts.confidence for ts in timed_segments
        if ts.confidence > 0
    ]

    metrics.segments_with_words = len(confidences)
    metrics.word_level_available = len(confidences) > 0

    if confidences:
        metrics.avg_confidence = sum(confidences) / len(confidences)
        metrics.min_confidence = min(confidences)
        metrics.max_confidence = max(confidences)

    # Flag low-confidence segments
    low_conf = flag_low_confidence_segments(timed_segments)
    metrics.low_confidence_count = len(low_conf)
    metrics.low_confidence_indices = low_conf

    return metrics
