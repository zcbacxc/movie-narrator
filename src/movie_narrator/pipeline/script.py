from typing import List

from ..config import get_settings
from ..models import Context, ScriptSegment
from ..utils.prompts import BEATS_PROMPT, EXPAND_PROMPT, build_cadence_hint
from ..utils.llm import get_llm_client
from ..utils.json_parser import extract_json
from ..tts.base import is_ci
from time import sleep

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

    Strategy: preserve the first ``hook_count`` segments (hooks must stay),
    then from the remaining pool select those whose length is closest to
    the median.  This avoids outlier sentences (very short or very long)
    and keeps the most "normal" content.

    If ``len(segments) <= target``, returns as-is (no padding).
    """
    if len(segments) <= target:
        return segments

    # Lock the first hook_count segments (hooks must be preserved)
    hook_count = min(3, target)
    locked = list(segments[:hook_count])
    pool = list(segments[hook_count:])

    need = target - hook_count
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
    )

    response = llm.client.chat.completions.create(
        model=llm.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=settings.research_temperature,
        max_tokens=settings.research_max_tokens,
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

    return [str(b).strip() for b in beats if str(b).strip()]


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
    for item in raw_segments:
        if isinstance(item, str):
            segments.append(ScriptSegment(text=item))
        elif isinstance(item, dict) and "text" in item:
            segments.append(ScriptSegment(text=item["text"]))

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
    target_count = ctx.metadata.get("prompt_target_sentences")

    for attempt in range(settings.script_retries):
        try:
            with get_llm_client() as llm:
                # Determine target sentence count
                if target_count and isinstance(target_count, int):
                    n = target_count
                else:
                    # No preset active — use legacy range "15-20", pick 18
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
                ctx.metadata["script_beat_count"] = len(beats)
                ctx.metadata["script_segment_count"] = len(segments)
                return ctx
        except Exception as e:
            if attempt == settings.script_retries - 1:
                # All retries exhausted.
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
