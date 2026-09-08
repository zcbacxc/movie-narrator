# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Clip matching step — match script segments to video scenes.

``pipeline.match`` is a package. The public step entry point ``match_clips``
and the main orchestration ``_match_clips_impl`` live here (in the package
namespace) so that patching ``movie_narrator.pipeline.match.probe`` in the
test suite keeps working — that helper is read as a module global at call
time. All lower-level helpers are split into sibling modules under this
package and re-exported below to preserve the ``pipeline.match.<name>``
public symbol surface.
"""

import json
import logging
from pathlib import Path
from typing import Any, List, Optional, Tuple, cast

from ...models import Context, MatchedClip, Scene, StepResult
from ...utils.optional_deps import probe

# Re-export every public (and internally-used) name so that both
# ``from movie_narrator.pipeline.match import <name>`` and
# ``pipeline.match.<name>`` resolve identically to the pre-split module.
from ._shared import _EMBEDDING_MODEL_NAME, _resolve_match_texts
from .caption import (
    _build_scene_captions,
    _build_scene_label as _build_scene_label,
)
from .embed import (
    _collect_visual_features,
    _cosine_top1 as _cosine_top1,
    _cosine_topk as _cosine_topk,
    _embed_texts,
    _load_embedding_model as _load_embedding_model,
)
from .fallback import _apply_diversity, _clamp_scene_window, _merge_short_scenes
from .score import (
    _DEFAULT_ACT_WEIGHTS,
    _RHYTHM_ADJUSTMENT_MAX,
    _RHYTHM_ZONE_TIMELINE_CENTER,
    _apply_rhythm_density_hint,
    _assign_segments_to_acts,
    _compute_rhythm_adjustment as _compute_rhythm_adjustment,
    _get_act_candidate_indices as _get_act_candidate_indices,
    _greedy_topk_assign,
    _partition_scenes_by_act,
)
from .transcribe import (
    _cache_key as _cache_key,
    _transcribe_video_audio,
    _video_audio_hash as _video_audio_hash,
)

logger = logging.getLogger(__name__)


def match_clips(ctx: Context) -> Context:
    """Match script segments to video scenes.

    Args:
        ctx: Pipeline execution context.

    Returns:
        Updated pipeline context with matched clips.
    """
    if not ctx.source_video_path:
        ctx.status.match = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no source video"
        return ctx
    if ctx.status.scene == "disabled":
        ctx.status.match = "disabled"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "scene disabled"
        return ctx
    if not ctx.scenes:
        ctx.status.match = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no scenes"
        return ctx
    if not ctx.timed_segments:
        ctx.status.match = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no timed segments"
        return ctx

    min_score = ctx.metadata.get("match_min_score", 0.25)
    clamp_min = ctx.metadata.get("match_speed_clamp_min", 0.85)
    clamp_max = ctx.metadata.get("match_speed_clamp_max", 1.25)
    merge_min = ctx.metadata.get("scene_merge_min_duration", 2.0)
    # Soft rhythm-zone hint — nudge the merge threshold based on
    # the dramatic-arc zones present in the beats (hook -> denser, settle ->
    # sparser). No-op when beats lack rhythm_zone markings.
    beats_meta = ctx.metadata.get("beats_meta", [])
    merge_min = _apply_rhythm_density_hint(merge_min, beats_meta)
    drop_min = ctx.metadata.get("match_drop_scene_min_duration", 0.4)
    output_dir = Path(ctx.output_dir)

    try:
        return _match_clips_impl(
            ctx, min_score, clamp_min, clamp_max, merge_min, drop_min, output_dir
        )
    except Exception as e:
        ctx.status.match = "failed"
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        logger.debug("match_clips failed", exc_info=True)
        return ctx


def _match_clips_impl(
    ctx: Context,
    min_score: float,
    clamp_min: float,
    clamp_max: float,
    merge_min: float,
    drop_min: float,
    output_dir: Path,
) -> Context:
    # i18n (v0.9.6): match in the target language. When the narration was
    # translated to subtitle_lang (translated_texts present & aligned), the
    # embedding re-rank matches the target-language text against the scene
    # captions; otherwise it matches the narration text (already in `lang`).
    # Record which language and which text source were used for auditability.
    match_texts = _resolve_match_texts(ctx)
    if ctx.translated_texts and len(ctx.translated_texts) == len(ctx.timed_segments):
        ctx.metadata["match_text_source"] = "translated"
        ctx.metadata["match_lang"] = ctx.metadata.get(
            "subtitle_lang", ctx.metadata.get("lang", "zh")
        )
    else:
        ctx.metadata["match_text_source"] = "narration"
        ctx.metadata["match_lang"] = ctx.metadata.get("lang", "zh")

    # Optionally merge short scenes to reduce extreme speed factors
    scenes = ctx.scenes
    scenes_in = len(ctx.scenes)  # original scene count
    if merge_min > 0:
        scenes = _merge_short_scenes(scenes, min_duration=merge_min)
        ctx.services.console.debug(
            f"  scene merge: {len(ctx.scenes)} -> {len(scenes)} scenes (min={merge_min}s)"
        )

    # Drop tiny scenes (e.g. <0.4s) that produce jarring sub-frame cuts.
    # If filtering would remove *all* scenes, keep the merged list as a
    # last-resort so matching still produces output.
    scenes_after_merge = len(scenes)  # count after merge, before drop
    if drop_min > 0 and scenes:
        filtered = [s for s in scenes if (s.end - s.start) >= drop_min]
        if filtered:
            scenes = filtered
            # Re-index after drop so scene indices are sequential.
            for i, s in enumerate(scenes):
                s.index = i

    # ── Scene filtering (intro skip + dark frame + highlight window) ──
    # All three are opt-in via job params. Order: intro → dark → window.
    # Each filter is independently toggleable; defaults preserve existing
    # behavior (no filtering when params are absent or zero).
    from ..scene_filter import (
        apply_source_window,
        filter_dark_scenes,
        filter_intro_scenes,
    )

    skip_intro = ctx.metadata.get("match_skip_intro_sec", 0.0)
    if skip_intro > 0:
        scenes, intro_dropped = filter_intro_scenes(scenes, skip_intro)
        if intro_dropped:
            ctx.services.console.debug(
                f"  intro skip: dropped {intro_dropped} scenes "
                f"(end <= {skip_intro}s) → {len(scenes)} remaining"
            )
            ctx.metadata["wp6_intro_dropped"] = intro_dropped

    dark_luma = ctx.metadata.get("match_drop_dark_luma", 0.0)
    if dark_luma > 0:
        scenes, dark_dropped = filter_dark_scenes(scenes, ctx.source_video_path, dark_luma)
        if dark_dropped:
            ctx.services.console.debug(
                f"  dark drop: removed {dark_dropped} scenes "
                f"(luma < {dark_luma}) → {len(scenes)} remaining"
            )
            ctx.metadata["wp6_dark_dropped"] = dark_dropped

    source_window = ctx.metadata.get("match_source_window")
    if source_window:
        scenes, win_dropped = apply_source_window(scenes, source_window)
        if win_dropped:
            ctx.services.console.debug(
                f"  highlight window {source_window}: "
                f"dropped {win_dropped} scenes → {len(scenes)} remaining"
            )
            ctx.metadata["wp6_window_dropped"] = win_dropped

    # Compute total scene span
    scene_start = min(s.start for s in scenes)
    scene_end = max(s.end for s in scenes)
    scene_span = scene_end - scene_start

    first_start = ctx.timed_segments[0].start
    last_end = ctx.timed_segments[-1].end
    narr_span = last_end - first_start

    # ── Act-weighted timeline partitioning ─────────────
    # When match_timeline_mode="weighted_acts", partition scenes into 4
    # equal-time buckets and assign narration segments to acts by weight.
    # Each segment's heuristic midpoint is mapped within its assigned
    # bucket (not the full timeline), and embedding candidates are
    # restricted to the bucket (+ adjacent overflow).
    timeline_mode = ctx.metadata.get("match_timeline_mode", "uniform")
    act_weights = ctx.metadata.get("match_act_weights", list(_DEFAULT_ACT_WEIGHTS))
    use_weighted_acts = (
        timeline_mode == "weighted_acts" and len(scenes) >= 8 and len(ctx.timed_segments) >= 4
    )
    # Top-K rerank params
    topk = ctx.metadata.get("match_topk", 5)
    reuse_penalty = ctx.metadata.get("match_topk_reuse_penalty", 0.15)
    if use_weighted_acts:
        act_scenes = _partition_scenes_by_act(scenes, n_acts=len(act_weights))
        act_assignments = _assign_segments_to_acts(len(ctx.timed_segments), act_weights)
        ctx.services.console.debug(
            f"  weighted_acts: {len(act_weights)} acts, "
            f"segments per act: {[act_assignments.count(a) for a in range(len(act_weights))]}"
        )
        # Pre-compute act -> segment indices map (O(n) once, not O(n²) per segment)
        act_seg_map: dict[int, list[int]] = {}
        for seg_idx, act_i in enumerate(act_assignments):
            act_seg_map.setdefault(act_i, []).append(seg_idx)
    else:
        act_scenes = None
        act_assignments = None

    # --- Heuristic baseline -------------------------------------------------
    # Map each narration midpoint proportionally onto the scene span, pick the
    # containing scene window. Produces a stable candidate per segment with
    # score=1.0 (plan T14 normative rule).
    #
    # When beat metadata (approx_ratio) is available from Phase 1,
    # use it as the primary time anchor — it's the LLM's estimate of where
    # in the film this plot point occurs, which is more accurate than
    # uniform narration-position mapping for improving D2 (scene-dialogue
    # relevance). Priority: beat anchor > weighted acts > uniform.
    beats_meta = ctx.metadata.get("beats_meta", [])
    use_beat_anchor = len(beats_meta) == len(ctx.timed_segments) and any(
        bm.get("approx_ratio") is not None for bm in beats_meta
    )
    if use_beat_anchor:
        n_with_ratio = sum(1 for bm in beats_meta if bm.get("approx_ratio") is not None)
        ctx.services.console.debug(
            f"  beat anchor: {n_with_ratio}/{len(beats_meta)} "
            f"segments have approx_ratio — using beat-based time anchoring"
        )

    heuristic = []
    for i, seg in enumerate(ctx.timed_segments):
        beat_meta = beats_meta[i] if i < len(beats_meta) else {}
        approx_ratio = beat_meta.get("approx_ratio")

        if approx_ratio is not None and 0.0 <= approx_ratio <= 1.0:
            # Use beat-based anchoring — LLM's estimate of where
            # this plot point occurs in the film timeline.
            src_mid = scene_start + approx_ratio * scene_span

            containing = None
            for scene in scenes:
                if scene.start <= src_mid <= scene.end:
                    containing = scene
                    break
            if containing is None:
                containing = scenes[0]
        elif use_weighted_acts:
            # Map within assigned act bucket
            assert act_assignments is not None and act_scenes is not None
            act_idx = act_assignments[i]
            bucket = act_scenes[act_idx]
            if not bucket:
                # Empty act — fall back to all scenes
                bucket = scenes
            b_start = min(s.start for s in bucket)
            b_end = max(s.end for s in bucket)
            b_span = b_end - b_start

            # Position within this segment's slot in the bucket
            act_segs = act_seg_map[act_idx]
            pos_in_act = act_segs.index(i)
            n_in_act = len(act_segs)
            if n_in_act > 1:
                local_ratio = (pos_in_act + 0.5) / n_in_act
            else:
                local_ratio = 0.5

            src_mid = b_start + local_ratio * b_span if b_span > 0 else b_start

            # Find containing scene within bucket
            containing = None
            for scene in bucket:
                if scene.start <= src_mid <= scene.end:
                    containing = scene
                    break
            if containing is None:
                containing = bucket[0]
        else:
            # Original uniform mapping
            narr_mid = (seg.start + seg.end) / 2.0
            if narr_span > 0:
                ratio = (narr_mid - first_start) / narr_span
                src_mid = scene_start + ratio * scene_span
            else:
                src_mid = scene_start

            containing = None
            for scene in scenes:
                if scene.start <= src_mid <= scene.end:
                    containing = scene
                    break
            if containing is None:
                containing = scenes[0]

        heuristic.append(
            {
                "segment_index": i,
                "text": seg.text,
                "narr_start": seg.start,
                "narr_end": seg.end,
                "scene_index": containing.index,
                "src_start": containing.start,
                "src_end": containing.end,
            }
        )

    # --- Optional embedding re-rank ----------------------------------------
    # Initialize tracking variables for match_summary (defined in all
    # branches below, but referenced at function end after the try/except).
    final: List[Tuple[dict[str, Any], float, Optional[Scene], str]] = []
    scene_captions: List[Tuple[str, bool]] = []
    usable_label_ratio: float = 0.0
    raw_scores: List[float] = []
    low_score_fallback_count = 0
    transcript: Optional[List[dict]] = None
    st_ok, st_hint = probe("sentence_transformers")
    if st_ok and len(scenes) > 1:
        try:
            # Try scene captioning via WhisperX (word-level alignment) or
            # faster-whisper (CTranslate2, works on Windows CPU). The
            # _transcribe_video_audio helper tries WhisperX first and falls
            # back to faster-whisper on import/runtime failure.
            transcript = None
            wx_ok, wx_hint = probe("whisperx")
            fw_ok, fw_hint = probe("faster_whisper")
            if not (wx_ok or fw_ok):
                ctx.services.console.inline_warn(
                    f"Neither WhisperX nor faster-whisper is available "
                    f"({wx_hint}; {fw_hint}); using fallback scene labels. "
                    f"Install with: pip install 'movie-narrator[ml]'"
                )
            elif ctx.source_video_path:
                wx_device = ctx.metadata.get("whisperx_device", "cpu")
                wx_model = ctx.metadata.get("whisperx_model", "medium")
                wx_lang = ctx.metadata.get("whisperx_language", "zh")
                ctx.services.console.debug(
                    f"  scene captioning: device={wx_device} model={wx_model} lang={wx_lang} "
                    f"(whisperx={wx_ok}, faster_whisper={fw_ok})"
                )
                transcript = _transcribe_video_audio(
                    ctx.source_video_path,
                    output_dir,
                    device=wx_device,
                    model_name=wx_model,
                    language=wx_lang,
                )
                if transcript:
                    ctx.services.console.debug(
                        f"  scene captions: {len(transcript)} transcript segments "
                        f"-> {len(scenes)} scenes"
                    )
                else:
                    ctx.services.console.inline_warn(
                        "scene transcription returned no results; using fallback scene labels"
                    )

            scene_captions = _build_scene_captions(scenes, transcript)

            # ── Vision captioner integration ───────────────
            # When vision_captioner is configured, use visual scene
            # descriptions to supplement or replace audio-transcript
            # captions. The stub returns the same placeholder format
            # as _build_scene_label (is_fake=True), so behavior is
            # unchanged. Real providers (BLIP, LLaVA) will produce
            # semantic descriptions (is_fake=False) that unlock
            # embedding re-rank even without WhisperX transcripts.
            vision_provider = ctx.metadata.get("vision_captioner", "none")
            if vision_provider != "none":
                try:
                    from ...vision import get_vision_captioner

                    captioner = get_vision_captioner(vision_provider)
                    vision_labels = captioner.caption_scenes(scenes, ctx.source_video_path)
                    is_stub = vision_provider == "stub"
                    scene_captions = [(label, is_stub) for label in vision_labels]
                    ctx.services.console.debug(
                        f"  vision captioner ({vision_provider}): "
                        f"{len(scene_captions)} captions, "
                        f"{'stub placeholders' if is_stub else 'real descriptions'}"
                    )
                except Exception as ve:
                    ctx.services.console.debug(
                        f"  vision captioner failed ({ve}); using audio-transcript captions"
                    )
                    logger.debug("vision captioner failed", exc_info=True)

            # ── Truth-in-match validation ──────────────
            # Detect fake captions (placeholder labels without real transcript).
            # If too many scenes have fake captions, embedding re-rank is
            # meaningless — it's matching narration against "scene 0 from
            # 0.0s to 15.0s" strings that carry no semantic information.
            # Threshold: if >70% of labels are fake, force heuristic.
            #
            # Use the is_fake flag from _build_scene_captions
            # instead of fragile string-pattern matching. This eliminates
            # the implicit dependency on the "scene {i} from {s1}s to {s2}s"
            # label template — if the template changes, the flag still works.
            fake_count = sum(1 for _, is_fake in scene_captions if is_fake)
            fake_ratio = fake_count / len(scene_captions) if scene_captions else 1.0
            usable_label_ratio = 1.0 - fake_ratio
            if fake_ratio > 0.7:
                ctx.services.console.inline_warn(
                    f"Scene captions are {fake_ratio:.0%} placeholder labels "
                    f"({fake_count}/{len(scene_captions)} scenes have no real transcript). "
                    f"Embedding re-rank would be misleading — forcing heuristic match. "
                    f"Install WhisperX with: pip install 'movie-narrator[ml]'"
                )
                ctx.metadata["match_captions_fake"] = True
                final = cast(
                    List[Tuple[dict[str, Any], float, Optional[Scene], str]],
                    [(h, 1.0, None, "heuristic") for h in heuristic],
                )
            else:
                ctx.metadata["match_captions_fake"] = False
                emb_model = ctx.metadata.get("embedding_model_name", _EMBEDDING_MODEL_NAME)
                # Extract labels from (label, is_fake) tuples
                scene_labels = [label for label, _ in scene_captions]
                scene_vecs = _embed_texts(scene_labels, emb_model)
                narration_vecs = _embed_texts(match_texts, emb_model)

                # ── Visual features (G9 stage-1 skeleton) ────────────────
                # Opt-in pipeline skeleton: extracts pure-FFmpeg low-level
                # visual features (luma + RGB histogram) per scene and records
                # availability in match_summary. It does NOT alter scene
                # selection (stage-1 features carry no semantics); it validates
                # the extraction pipeline so a stage-2 semantic encoder can be
                # swapped in through the same interface.
                if ctx.metadata.get("match_visual_features", False):
                    _collect_visual_features(ctx, scene_vecs, scenes)
                # ── End visual features ──────────────────────────────────

                # Greedy top-K assignment with reuse penalty
                topk_results = _greedy_topk_assign(
                    narration_vecs=narration_vecs,
                    scene_vecs=scene_vecs,
                    scenes=scenes,
                    topk=topk,
                    reuse_penalty=reuse_penalty,
                    reuse_window=ctx.metadata.get("match_diversity_window", 3),
                    use_weighted_acts=use_weighted_acts,
                    act_assignments=act_assignments if use_weighted_acts else None,
                    act_scenes=act_scenes if use_weighted_acts else None,
                    act_weights=act_weights if use_weighted_acts else None,
                    beats_meta=beats_meta,
                    scene_start=scene_start,
                    scene_span=scene_span,
                )

                for i, (scene_idx, score, source) in enumerate(topk_results):
                    best_scene = scenes[scene_idx]
                    final.append((heuristic[i], score, best_scene, source))
                    # Collect raw embedding score (before low-score
                    # fallback overrides it to 1.0). Lets match_summary
                    # distinguish "matched well" from "matched poorly".
                    raw_scores.append(score)
        except Exception as e:
            ctx.services.console.inline_warn(
                f"embedding re-rank unavailable ({e}); using heuristic"
            )
            logger.debug("embedding re-rank failed", exc_info=True)
            final = cast(
                List[Tuple[dict[str, Any], float, Optional[Scene], str]],
                [(h, 1.0, None, "heuristic") for h in heuristic],
            )
    else:
        final = cast(
            List[Tuple[dict[str, Any], float, Optional[Scene], str]],
            [(h, 1.0, None, "heuristic") for h in heuristic],
        )

    # --- Build matched clips with speed clamp -------------------------------
    matched_clips = []
    video_total_duration = scene_end  # total video duration for boundary clamping

    for h, score, best_scene, source in final:  # type: ignore[assignment]
        if best_scene is not None:
            scene_obj: Scene = best_scene
        else:
            scene_obj = next(s for s in scenes if s.index == h["scene_index"])
        if score < min_score:
            # Embedding score too low — fall back to heuristic for this segment
            # instead of dropping it entirely. Dropping causes missing video
            # footage for that narration segment.
            ctx.services.console.debug(
                f"  segment {h['segment_index']}: embedding score {score:.3f} < "
                f"min_score {min_score:.3f}; falling back to heuristic"
            )
            scene_obj = next(s for s in scenes if s.index == h["scene_index"])
            source = "heuristic"
            score = 1.0
            low_score_fallback_count += 1  # count low-score fallbacks

        narr_duration = cast(float, h["narr_end"]) - cast(float, h["narr_start"])
        # Apply speed clamp: adjust src_start/src_end so factor stays in [clamp_min, clamp_max]
        clamped_start, clamped_end = _clamp_scene_window(
            scene_obj.start,
            scene_obj.end,
            narr_duration,
            video_start=0.0,
            video_end=video_total_duration,
            clamp_min=clamp_min,
            clamp_max=clamp_max,
        )

        matched_clips.append(
            MatchedClip(
                segment_index=h["segment_index"],
                text=h["text"],
                narr_start=h["narr_start"],
                narr_end=h["narr_end"],
                src_start=clamped_start,
                src_end=clamped_end,
                score=score,
                scene_index=scene_obj.index,
                source=source,
            )
        )

    ctx.matched_clips = matched_clips

    # ── Diversity post-processing ──────────────────────
    # Prevent consecutive scene reuse: if the same scene index appears
    # more than match_max_scene_reuse times within match_diversity_window
    # segments, swap later occurrences to the nearest unused scene.
    diversity_swaps, diversity_swaps_log = _apply_diversity(
        matched_clips,
        scenes,
        window=ctx.metadata.get("match_diversity_window", 3),
        max_reuse=ctx.metadata.get("match_max_scene_reuse", 2),
    )

    # ── v0.5.11: Match quality scoring aggregation ──────────
    # Compute per-clip composite scores across embedding + rhythm + diversity
    # dimensions.  Rhythm scores are extracted from the greedy_topk_assign
    # results if available; otherwise only embedding + diversity are used.
    from ...utils.match_quality import score_clips, aggregate_match_quality

    # Extract rhythm scores from topk_results if embedding path ran.
    # The rhythm adjustment was used internally by _greedy_topk_assign
    # but not persisted per-clip. For v0.5.11, we recompute a simplified
    # rhythm score from beats_meta rhythm_zone if available.
    rhythm_scores: list[Optional[float]] = []
    for i, mc in enumerate(matched_clips):
        if i < len(beats_meta) and beats_meta[i].get("rhythm_zone"):
            # Map rhythm zone to a 0-1 score based on how well the
            # clip's scene aligns with the expected rhythm zone.
            # This is a simplified heuristic; the full rhythm adjustment
            # is computed inside _greedy_topk_assign.
            # Simple mapping: high-energy zones get higher rhythm scores
            # when matched via embedding (content matches energy).
            if mc.source in ("embedding", "embedding_topk", "embedding_top1"):
                rhythm_scores.append(0.8)  # default good alignment
            else:
                rhythm_scores.append(None)
        else:
            rhythm_scores.append(None)

    score_clips(
        matched_clips,
        rhythm_scores=rhythm_scores,
        diversity_window=ctx.metadata.get("match_diversity_window", 3),
        diversity_max_reuse=ctx.metadata.get("match_max_scene_reuse", 2),
    )

    # Aggregate match quality summary
    match_quality = aggregate_match_quality(matched_clips)
    ctx.metadata["match_quality"] = match_quality.to_dict()
    if match_quality.low_quality_count > 0:
        ctx.services.console.inline_warn(
            f"Match quality: {match_quality.low_quality_count} clip(s) "
            f"have low composite score (< 0.4)."
        )

    # Log speed factor stats + collect for match_summary
    speed_factors: List[float] = []
    if matched_clips:
        for mc in matched_clips:
            narr_dur = mc.narr_end - mc.narr_start
            if narr_dur > 0:
                speed_factors.append((mc.src_end - mc.src_start) / narr_dur)
        if speed_factors:
            ctx.services.console.debug(
                f"  speed factors: min={min(speed_factors):.2f}x max={max(speed_factors):.2f}x "
                f"avg={sum(speed_factors) / len(speed_factors):.2f}x (clamp={clamp_min}~{clamp_max}x)"
            )

    matches_path = output_dir / "matches.json"
    matches_path.write_text(
        json.dumps([m.model_dump() for m in matched_clips], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ── match_summary for metadata.json (full schema) ──────
    # Records the match quality breakdown so manual QA can verify
    # the main path isn't "全 heuristic 糊弄" (O9/O10 in checklist).
    # Schema per CORE_ENGINE_TREATMENT_PLAN §5.2.3.
    # Sources can be "embedding_topk", "embedding_top1", or "heuristic"
    embedding_count = sum(
        1 for mc in matched_clips if mc.source in ("embedding", "embedding_topk", "embedding_top1")
    )
    heuristic_count = sum(1 for mc in matched_clips if mc.source == "heuristic")
    topk_count = sum(1 for mc in matched_clips if mc.source == "embedding_topk")
    top1_count = sum(1 for mc in matched_clips if mc.source == "embedding_top1")
    total = len(matched_clips)

    # score stats: only for source==embedding clips that were adopted
    # (i.e. did NOT fall back to heuristic due to low score)
    adopted_embedding_scores = [
        mc.score
        for mc in matched_clips
        if mc.source in ("embedding", "embedding_topk", "embedding_top1")
    ]

    def _stats(values: List[float]) -> Optional[dict]:
        if not values:
            return None
        return {
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "avg": round(sum(values) / len(values), 4),
        }

    def _stats_with_n(values: List[float]) -> Optional[dict]:
        if not values:
            return None
        return {
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "avg": round(sum(values) / len(values), 4),
            "n": len(values),
        }

    # Determine degraded_reason
    degraded_reason: Optional[str] = None
    if ctx.metadata.get("match_captions_fake"):
        degraded_reason = "fake_captions"
    elif heuristic_count == total and total > 0:
        degraded_reason = "all_heuristic"

    ctx.metadata["match_summary"] = {
        "version": 1,
        "status": "success",
        "segments": total,
        "scenes_in": scenes_in,
        "scenes_after_merge": scenes_after_merge,
        "scenes_after_drop": len(scenes),
        "merge_min_duration": merge_min,
        "drop_min_duration": drop_min,
        "min_score": min_score,
        "speed_clamp": [clamp_min, clamp_max],
        "source_counts": {
            "embedding": embedding_count,
            "embedding_topk": topk_count,
            "embedding_top1": top1_count,
            "heuristic": heuristic_count,
        },
        "heuristic_ratio": round(heuristic_count / total, 4) if total else 1.0,
        "embedding_ratio": round(embedding_count / total, 4) if total else 0.0,
        "score": _stats(adopted_embedding_scores),
        "raw_score": _stats_with_n(raw_scores),
        "speed_factor": _stats(speed_factors),
        "low_score_fallback_count": low_score_fallback_count,
        "captioning": {
            "used": transcript is not None,
            "usable_label_ratio": round(usable_label_ratio, 4) if scene_captions else 0.0,
            "cached": ctx.metadata.get("match_transcript_cached", False),
            "language": ctx.metadata.get("whisperx_language", "zh"),
            "model": ctx.metadata.get("whisperx_model", "medium"),
        },
        "embedding_model": ctx.metadata.get("embedding_model_name", _EMBEDDING_MODEL_NAME),
        "degraded_reason": degraded_reason,
        "diversity": {
            "swaps": diversity_swaps,
            "swaps_log": diversity_swaps_log,
            "window": ctx.metadata.get("match_diversity_window", 3),
            "max_reuse": ctx.metadata.get("match_max_scene_reuse", 2),
        },
        "timeline": {
            "mode": (
                "beat_anchor"
                if use_beat_anchor
                else "weighted_acts"
                if use_weighted_acts
                else "uniform"
            ),
            "beat_anchor": use_beat_anchor,
            "beat_anchored_count": (
                sum(1 for bm in beats_meta if bm.get("approx_ratio") is not None)
                if use_beat_anchor
                else 0
            ),
            "act_weights": act_weights if use_weighted_acts else None,
            "segments_per_act": (
                [
                    cast(list[int], act_assignments).count(a)
                    for a in range(len(cast(list[float], act_weights)))
                ]
                if use_weighted_acts
                else None
            ),
        },
        "topk": {
            "k": topk,
            "reuse_penalty": reuse_penalty,
            "topk_count": topk_count,
            "top1_count": top1_count,
        },
        "rhythm_scoring": {
            "enabled": any(bm.get("rhythm_zone") is not None for bm in beats_meta),
            "zones": {
                z: sum(1 for bm in beats_meta if bm.get("rhythm_zone") == z)
                for z in _RHYTHM_ZONE_TIMELINE_CENTER
            },
            "adjustment_max": _RHYTHM_ADJUSTMENT_MAX,
        },
        # v0.5.11: composite match quality summary
        "match_quality": ctx.metadata.get("match_quality", {}),
        # Back-compat fields (kept for existing consumers)
        "total": total,
        "embedding": embedding_count,
        "heuristic": heuristic_count,
        "captions_fake": ctx.metadata.get("match_captions_fake", False),
    }

    ctx.status.match = "success"
    return ctx


# Backwards-compatible alias for in-process callers that imported this
# from the module top-level before the refactor.
match_clips_original = _match_clips_impl
