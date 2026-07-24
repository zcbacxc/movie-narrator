from typing import List
import re

from ..config import get_settings
from ..models import Context, ScriptSegment
from ..utils.prompts import BEATS_PROMPT, EXPAND_PROMPT, build_cadence_hint, build_set_pieces_hint, build_hook_hint
from ..utils.llm import get_llm_client
from ..utils.json_parser import extract_json
from ..tts.base import is_ci
from time import sleep


# ── WP5: max_chars hard truncation ──────────────────────
# LLM may ignore the max_chars prompt instruction. This post-processing
# step hard-truncates any sentence exceeding the limit, cutting at the
# last punctuation mark before the limit for natural breaks.
_PUNCT_PATTERN = re.compile(r'[。！？；，、…\.,!?;]')


def _truncate_to_max_chars(text: str, max_chars: int) -> str:
    """Hard-truncate text to max_chars, preferring natural punctuation breaks."""
    if len(text) <= max_chars:
        return text
    # Find the last punctuation mark before max_chars
    truncated = text[:max_chars]
    match = None
    for m in _PUNCT_PATTERN.finditer(truncated):
        match = m  # keep the last match
    if match:
        return truncated[: match.end()].rstrip()
    # No punctuation found — hard cut
    return truncated.rstrip()

# CI-only fallback: used when LLM is unreachable in CI environment
# to allow full pipeline testing. Never used for real users.
_CI_MOCK_SEGMENTS = [
    "{movie_name}是一部精彩的电影，",
    "讲述了令人难忘的故事。",
    "每一个场景都扣人心弦，令人回味无穷。",
    "不容错过的经典之作。",
]


# ── Fallback trim ───────────────────────────────────────────


def _trim_segments(segments: List[ScriptSegment], target: int) -> List[ScriptSegment]:
    """Trim segments to exactly *target* count if over.

    Strategy: preserve the first ``hook_count`` segments (hooks must stay)
    and the last segment (tail climax/outro must stay), then from the
    remaining pool select those whose length is closest to the median.
    This avoids outlier sentences (very short or very long) and keeps
    the most "normal" content.

    If ``len(segments) <= target``, returns as-is (no padding).
    """
    if len(segments) <= target:
        return segments

    # Lock the first hook_count segments (hooks must be preserved)
    hook_count = min(3, target)
    # ST-06: Also lock the last segment (tail climax/outro protection)
    # Only lock tail if we have enough segments to spare.
    lock_tail = target > hook_count + 1 and len(segments) > target + 1

    locked = list(segments[:hook_count])
    if lock_tail:
        locked.append(segments[-1])
        pool = list(segments[hook_count:-1])
    else:
        pool = list(segments[hook_count:])

    need = target - len(locked)
    if need <= 0:
        return locked[:target]

    # Rank pool by proximity to median length
    lengths = sorted(len(s.text) for s in pool)
    median_len = lengths[len(lengths) // 2]
    ranked = sorted(pool, key=lambda s: abs(len(s.text) - median_len))
    selected = ranked[:need]

    # Reassemble in original order
    all_selected = locked + selected
    # Use stable sort by original index to preserve chronological order
    original_indices = {id(s): i for i, s in enumerate(segments)}
    all_selected.sort(key=lambda s: original_indices.get(id(s), 0))
    return all_selected


# ── Phase 1: plot beat extraction ──────────────────────────


def _generate_plot_beats(
    ctx: Context, settings, llm, target_count: int
) -> List[str]:
    """Phase 1: Extract exactly *target_count* plot beats from the movie.

    Uses low temperature (research_temperature) for structured extraction.
    Raises ValueError if the LLM doesn't return exactly target_count beats.
    """
    research_block = ""
    if ctx.research and ctx.research.summary:
        research_block = (
            f"\nResearch context: {ctx.research.summary}\n"
            f"Genres: {', '.join(ctx.research.genres)}\n"
        )

    prompt = BEATS_PROMPT.format(
        movie=ctx.movie_name,
        style=ctx.style,
        research=research_block,
        target_count=target_count,
        set_pieces_hint=build_set_pieces_hint(ctx.metadata.get("set_pieces")),
    )

    # ST-09: Scale max_tokens by target_count to avoid truncation on
    # high-segment presets (e.g. douyin 120s → n=36). Each beat needs
    # ~60 tokens; floor at the configured research_max_tokens.
    scaled_max_tokens = max(settings.research_max_tokens, target_count * 60)

    response = llm.client.chat.completions.create(
        model=llm.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=settings.research_temperature,
        max_tokens=scaled_max_tokens,
    )
    raw = response.choices[0].message.content or ""
    data = extract_json(raw)
    beats = data.get("beats", [])

    if not isinstance(beats, list):
        raise ValueError(f"Phase 1: 'beats' is not a list (got {type(beats).__name__})")
    if len(beats) == 0:
        raise ValueError("Phase 1: LLM returned zero beats")
    if len(beats) != target_count:
        raise ValueError(
            f"Phase 1: expected {target_count} beats, got {len(beats)}"
        )

    # Filter out None / non-string / empty beats.
    # str(None) = "None" is truthy and would silently pass the old
    # `if str(b).strip()` check, producing a meaningless "None" beat.
    # EP2: Also extract beat metadata (act, approx_ratio) when LLM
    # returns structured objects. Falls back to plain strings for
    # backward compatibility.
    cleaned = []
    beats_meta: list[dict] = []
    for b in beats:
        if b is None:
            continue
        if isinstance(b, dict):
            text = str(b.get("text", "")).strip()
            if not text or text.lower() == "none":
                continue
            act = b.get("act")
            try:
                act = int(act) if act is not None else None
                if act is not None and not (1 <= act <= 4):
                    act = None
            except (TypeError, ValueError):
                act = None
            ratio = b.get("approx_ratio")
            try:
                ratio = float(ratio) if ratio is not None else None
                if ratio is not None:
                    ratio = max(0.0, min(1.0, ratio))  # clamp to [0, 1]
            except (TypeError, ValueError):
                ratio = None
            cleaned.append(text)
            beats_meta.append({"text": text, "act": act, "approx_ratio": ratio})
        else:
            text = str(b).strip()
            if not text or text.lower() == "none":
                continue
            cleaned.append(text)
            beats_meta.append({"text": text, "act": None, "approx_ratio": None})
    if len(cleaned) != target_count:
        raise ValueError(
            f"Phase 1: after filtering None/empty beats, expected {target_count}, got {len(cleaned)}"
        )
    # EP2: Store beat metadata for match.py time anchoring
    ctx.metadata["beats_meta"] = beats_meta
    return cleaned


# ── Phase 2: beat expansion ────────────────────────────────


def _expand_beats_to_script(
    ctx: Context, settings, llm, beats: List[str], target_count: int
) -> List[ScriptSegment]:
    """Phase 2: Expand each beat into exactly one narration segment.

    Uses moderate temperature (script_expand_temperature) for style
    expression while keeping count controlled.
    """
    tags = ctx.metadata.get("narration_preset_tags", {})
    max_chars = ctx.metadata.get("prompt_max_chars_per_sentence", 15)
    hook_seconds = ctx.metadata.get("prompt_hook_seconds", 3)

    # Format beats as a numbered list for the prompt
    beats_text = "\n".join(f"{i+1}. {b}" for i, b in enumerate(beats))

    prompt = EXPAND_PROMPT.format(
        movie=ctx.movie_name,
        style=ctx.style,
        duration=ctx.duration,
        cadence_hint=build_cadence_hint(
            cadence=tags.get("prompt_cadence", ""),
            connectors=tags.get("prompt_connectors", ""),
            register=tags.get("prompt_register", ""),
        ),
        beats=beats_text,
        target_count=target_count,
        max_chars=max_chars,
        hook_seconds=hook_seconds,
        hook_hint=build_hook_hint(ctx.metadata.get("hook_templates"), ctx.movie_name),
    )

    response = llm.client.chat.completions.create(
        model=llm.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=settings.script_expand_temperature,
        max_tokens=settings.script_max_tokens,
    )
    raw = response.choices[0].message.content or ""
    data = extract_json(raw)
    raw_segments = data.get("segments", [])

    segments = []
    truncated_count = 0
    truncated_details: list[dict] = []
    for item in raw_segments:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict) and "text" in item:
            text = str(item["text"]).strip()
        else:
            continue
        # Skip empty / whitespace-only segments — they'd produce
        # silent TTS audio and break the count contract.
        if text:
            original_len = len(text)
            # WP5: hard-truncate to max_chars (LLM may ignore prompt)
            text = _truncate_to_max_chars(text, max_chars)
            if len(text) < original_len:
                truncated_count += 1
                truncated_details.append({
                    "original_len": original_len,
                    "truncated_len": len(text),
                })
            if text:
                segments.append(ScriptSegment(text=text))

    # WP5: audit metadata — track truncation for diagnostics
    if truncated_count > 0:
        ctx.metadata["script_truncated"] = {
            "count": truncated_count,
            "max_chars": max_chars,
            "details": truncated_details,
        }

    if not segments:
        raise ValueError("Phase 2: LLM returned zero segments")

    return segments


# ── Main entry point ───────────────────────────────────────


def generate_script(ctx: Context) -> Context:
    """Two-phase script generation: plot beats -> narration expansion -> trim.

    Phase 1 extracts exactly N plot beats (low temperature, structured).
    Phase 2 expands each beat into one narration line (style tags applied).
    Fallback trim ensures exactly N segments even if LLM overshoots.

    The retry loop wraps both phases together.  CI mode falls back to
    mock content (same as v0.4.15).
    """
    settings = get_settings()
    base_count = ctx.metadata.get("prompt_target_sentences")
    seg_duration = ctx.metadata.get("prompt_target_segment_duration")

    for attempt in range(settings.script_retries):
        try:
            with get_llm_client() as llm:
                # Determine target sentence count.
                # If preset defines target_segment_duration, compute count
                # dynamically from the actual target duration so that
                # longer videos get more sentences (not longer sentences).
                # This keeps per-sentence length natural (19-25 chars)
                # regardless of total video duration.
                #
                # Example: bilibili-long (seg_duration=7.5s)
                #   60s  → 8 sentences  (7.5s each)
                #   90s  → 12 sentences (7.5s each)
                #   120s → 16 sentences (7.5s each)
                if seg_duration and isinstance(seg_duration, (int, float)) and seg_duration > 0:
                    n = max(1, round(ctx.duration / seg_duration))
                elif base_count and isinstance(base_count, int):
                    n = base_count
                else:
                    # No preset active — use legacy default
                    n = 18

                # Phase 1: extract plot beats
                beats = _generate_plot_beats(ctx, settings, llm, n)

                # Phase 2: expand beats into narration segments
                segments = _expand_beats_to_script(ctx, settings, llm, beats, n)

                # Fallback: trim to exactly n if LLM overshot
                segments = _trim_segments(segments, n)

                ctx.segments = segments
                ctx.metadata["script_source"] = "llm"
                ctx.metadata["script_phase"] = "two-phase"
                ctx.metadata["script_target_count"] = n
                ctx.metadata["script_beat_count"] = len(beats)
                ctx.metadata["script_segment_count"] = len(segments)
                return ctx
        except Exception as e:
            if attempt == settings.script_retries - 1:
                # All retries exhausted — log diagnostic info before failing.
                # The raw LLM output is critical for debugging prompt/count
                # issues that don't show up in the exception message alone.
                ctx.services.console.debug(
                    f"  generate_script: all {settings.script_retries} attempts failed. "
                    f"Last error: {e}"
                )
                # In CI: fall back to mock content (with warning) so the
                # full pipeline can be exercised without an LLM.
                # In production: hard fail — user must know the script
                # is not real, no silent fake content.
                if is_ci():
                    ctx.services.console.inline_warn(
                        f"LLM unreachable (CI mode): using mock script. {e}"
                    )
                    ctx.segments = [
                        ScriptSegment(text=s.format(movie_name=ctx.movie_name))
                        for s in _CI_MOCK_SEGMENTS
                    ]
                    ctx.metadata["script_source"] = "ci_mock"
                    ctx.metadata["script_degraded"] = True
                    return ctx
                raise RuntimeError(
                    f"LLM script generation failed after {settings.script_retries} attempts: {e}. "
                    f"Check your LLM configuration (MN_LLM_BASE_URL, MN_LLM_API_KEY, MN_LLM_MODEL) "
                    f"and network connectivity."
                ) from e
            sleep(settings.script_retry_delay)
    return ctx
