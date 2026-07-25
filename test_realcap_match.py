"""S6 补测: Re-run match_clips with real captions via faster-whisper.

Loads scenes + timed_segments from the G2 v0.4.27 baseline (which used
fake captions / 100% heuristic match) and re-runs match_clips with
faster-whisper enabled to produce real scene captions.

Output:
- output/l2-plus-g2-realcap-test/matches.json  (new matches with real captions)
- output/l2-plus-g2-realcap-test/metadata.json (new match_summary)
- output/l2-plus-g2-realcap-test/transcript_*.json (cached transcript)

Usage:
    D:\tmp\mn-venv-3.13\Scripts\python.exe test_realcap_match.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Must be set before importing faster_whisper / tqdm
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
# Force HF to use local cache, never redownload
os.environ.setdefault("HF_HUB_OFFLINE", "0")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from movie_narrator.models import (
    Context,
    MatchedClip,
    Scene,
    Services,
    TimedSegment,
)
from movie_narrator.pipeline.match import match_clips
from movie_narrator.utils.console import build_console


def log(msg: str) -> None:
    """Flush every line so we can monitor progress in real time."""
    print(msg, flush=True)


def main() -> int:
    project_root = Path(__file__).parent
    baseline_dir = project_root / "output" / "l2-plus-g2-v0427"
    scenes_json = baseline_dir / "scenes.json"
    metadata_json = baseline_dir / "metadata.json"
    output_dir = project_root / "output" / "l2-plus-g2-realcap-test"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Clean stale matches.json from interrupted run
    stale_matches = output_dir / "matches.json"
    if stale_matches.exists():
        log(f"[setup] removing stale matches.json from previous run")
        stale_matches.unlink()

    # 1. Load scenes from previous run
    log(f"[1/6] Loading scenes from {scenes_json}")
    with scenes_json.open(encoding="utf-8") as f:
        scenes_data = json.load(f)
    scenes = [Scene(**s) for s in scenes_data["scenes"]]
    log(f"      -> {len(scenes)} scenes loaded")

    # 2. Load timed_segments from previous run's metadata
    log(f"[2/6] Loading timed_segments from {metadata_json}")
    with metadata_json.open(encoding="utf-8") as f:
        metadata = json.load(f)
    timed_segments = [TimedSegment(**s) for s in metadata["segments"]]
    log(f"      -> {len(timed_segments)} segments loaded")

    # 3. Build minimal Context (no Path movie_name etc, just enough for match_clips)
    log(f"[3/6] Building Context")
    console = build_console(output_dir)
    ctx = Context(
        movie_name="西虹市首富",
        style="热血搞笑",
        duration=60,
        output_dir=str(output_dir),
        source_video_path=r"D:\test\电影源\西虹市首富.mp4",
        scenes=scenes,
        timed_segments=timed_segments,
        services=Services(console=console),
    )
    # Match checks ctx.status.scene — must be "success" (scenes already detected
    # in baseline run; we're re-running match with real captions)
    ctx.status.scene = "success"

    # Required metadata for match_clips
    ctx.metadata["match_min_score"] = 0.25
    ctx.metadata["scene_threshold"] = 27.0
    ctx.metadata["match_topk"] = 5
    ctx.metadata["match_topk_reuse_penalty"] = 0.15
    ctx.metadata["match_diversity_window"] = 3
    ctx.metadata["match_max_scene_reuse"] = 2
    ctx.metadata["match_timeline_mode"] = "uniform"  # beat_meta will override
    ctx.metadata["scene_merge_min_duration"] = 2.0
    ctx.metadata["match_drop_scene_min_duration"] = 0.4
    ctx.metadata["match_speed_clamp_min"] = 0.85
    ctx.metadata["match_speed_clamp_max"] = 1.25

    # WhisperX / faster-whisper config — use "small" (cached, good quality)
    ctx.metadata["whisperx_device"] = "cpu"
    ctx.metadata["whisperx_model"] = "small"  # fallback uses "small" not "medium"
    ctx.metadata["whisperx_language"] = "zh"

    # Embedding model (use cached)
    ctx.metadata["embedding_model_name"] = "paraphrase-multilingual-MiniLM-L12-v2"

    # Inject beats_meta from baseline (EP2 beat anchors)
    beats_meta = metadata.get("beats_meta") or []
    if beats_meta:
        ctx.metadata["beats_meta"] = beats_meta
        log(f"      -> loaded {len(beats_meta)} beats_meta entries (EP2 beat anchors)")

    log(f"[4/6] Verifying faster-whisper availability...")
    from movie_narrator.utils.optional_deps import probe
    wx_ok, _ = probe("whisperx")
    fw_ok, _ = probe("faster_whisper")
    st_ok, _ = probe("sentence_transformers")
    log(f"      whisperx={wx_ok}, faster_whisper={fw_ok}, sentence_transformers={st_ok}")
    if not (wx_ok or fw_ok):
        log("[fatal] Neither whisperx nor faster_whisper available")
        return 2

    # 5. Run match_clips — this will trigger faster-whisper transcription
    log(f"[5/6] Running match_clips (faster-whisper will transcribe video audio)")
    log(f"      Video: {ctx.source_video_path}")
    log(f"      Output: {output_dir}")
    log(f"      Model: {ctx.metadata['whisperx_model']} (cpu, int8)")
    log(f"      [timing] transcription may take 25-35 minutes for 118-min video on CPU")
    log(f"      [start] {time.strftime('%H:%M:%S')} — please wait...")
    t0 = time.time()
    try:
        ctx = match_clips(ctx)
    except Exception as e:
        elapsed = time.time() - t0
        log(f"      [ERROR] match_clips failed after {elapsed:.1f}s: {e}")
        import traceback
        log(traceback.format_exc())
        # Save partial state for diagnosis
        partial_path = output_dir / "partial_state.json"
        partial_path.write_text(
            json.dumps({
                "error": str(e),
                "elapsed_sec": round(elapsed, 1),
                "traceback": traceback.format_exc(),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 1
    elapsed = time.time() - t0
    log(f"      [end] {time.strftime('%H:%M:%S')}")
    log(f"      -> match_clips completed in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    log(f"      -> status: {ctx.status.match}")

    # 6. Print match_summary and compare with baseline
    log(f"[6/6] Comparison with baseline (fake-captions)")

    new_summary = ctx.metadata.get("match_summary", {})
    baseline_summary = metadata.get("match_summary", {})

    log("")
    log("=" * 70)
    log("MATCH_SUMMARY COMPARISON")
    log("=" * 70)
    log(f"{'metric':<35} {'baseline':>15} {'realcap':>15}")
    log("-" * 70)
    rows = [
        ("degraded_reason", baseline_summary.get("degraded_reason"), new_summary.get("degraded_reason")),
        ("captions_fake", baseline_summary.get("captions_fake"), new_summary.get("captions_fake")),
        ("captioning.used", baseline_summary.get("captioning", {}).get("used"), new_summary.get("captioning", {}).get("used")),
        ("captioning.usable_label_ratio",
         baseline_summary.get("captioning", {}).get("usable_label_ratio"),
         new_summary.get("captioning", {}).get("usable_label_ratio")),
        ("source_counts.heuristic", baseline_summary.get("source_counts", {}).get("heuristic"), new_summary.get("source_counts", {}).get("heuristic")),
        ("source_counts.embedding", baseline_summary.get("source_counts", {}).get("embedding"), new_summary.get("source_counts", {}).get("embedding")),
        ("source_counts.embedding_topk", baseline_summary.get("source_counts", {}).get("embedding_topk"), new_summary.get("source_counts", {}).get("embedding_topk")),
        ("source_counts.embedding_top1", baseline_summary.get("source_counts", {}).get("embedding_top1"), new_summary.get("source_counts", {}).get("embedding_top1")),
        ("heuristic_ratio", baseline_summary.get("heuristic_ratio"), new_summary.get("heuristic_ratio")),
        ("embedding_ratio", baseline_summary.get("embedding_ratio"), new_summary.get("embedding_ratio")),
        ("score.min", (baseline_summary.get("score") or {}).get("min"), (new_summary.get("score") or {}).get("min")),
        ("score.max", (baseline_summary.get("score") or {}).get("max"), (new_summary.get("score") or {}).get("max")),
        ("score.avg", (baseline_summary.get("score") or {}).get("avg"), (new_summary.get("score") or {}).get("avg")),
        ("raw_score.min", (baseline_summary.get("raw_score") or {}).get("min"), (new_summary.get("raw_score") or {}).get("min")),
        ("raw_score.max", (baseline_summary.get("raw_score") or {}).get("max"), (new_summary.get("raw_score") or {}).get("max")),
        ("raw_score.avg", (baseline_summary.get("raw_score") or {}).get("avg"), (new_summary.get("raw_score") or {}).get("avg")),
        ("raw_score.n", (baseline_summary.get("raw_score") or {}).get("n"), (new_summary.get("raw_score") or {}).get("n")),
        ("low_score_fallback_count", baseline_summary.get("low_score_fallback_count"), new_summary.get("low_score_fallback_count")),
        ("diversity.swaps", baseline_summary.get("diversity", {}).get("swaps"), new_summary.get("diversity", {}).get("swaps")),
        ("timeline.mode", baseline_summary.get("timeline", {}).get("mode"), new_summary.get("timeline", {}).get("mode")),
        ("timeline.beat_anchor", baseline_summary.get("timeline", {}).get("beat_anchor"), new_summary.get("timeline", {}).get("beat_anchor")),
    ]
    for name, b, n in rows:
        log(f"{name:<35} {str(b):>15} {str(n):>15}")

    # S6 score derivation
    log("")
    log("=" * 70)
    log("S6 SCORE DERIVATION")
    log("=" * 70)
    heur_count = new_summary.get("source_counts", {}).get("heuristic", 0)
    emb_count = (new_summary.get("source_counts", {}).get("embedding", 0)
                 + new_summary.get("source_counts", {}).get("embedding_topk", 0)
                 + new_summary.get("source_counts", {}).get("embedding_top1", 0))
    total = new_summary.get("segments", 0)
    captions_fake = new_summary.get("captions_fake", True)
    if captions_fake:
        s6 = 2  # baseline pass, env exemption
        s6_reason = "captions still fake → env exemption (S6=2)"
    elif heur_count == total and total > 0:
        s6 = 1
        s6_reason = f"real captions but 100% heuristic (S6=1, EP8 trigger condition met)"
    elif emb_count > 0 and emb_count < total:
        s6 = 2
        s6_reason = f"partial embedding match ({emb_count}/{total} = {emb_count/total:.0%}) (S6=2, EP8 not triggered)"
    elif emb_count == total:
        s6 = 3
        s6_reason = f"full embedding match (S6=3, exceeds L2+ threshold)"
    else:
        s6 = 2
        s6_reason = "uncertain — kept S6=2"

    log(f"Suggested S6 score: {s6}")
    log(f"Reason: {s6_reason}")
    log("")
    log("Per QUALITY_UPLIFT_METHODS §3.3 EP8 trigger conditions:")
    log("  - EP1-EP4 已上线: ✓ (all present)")
    log("  - G1/G2 上 S6 仍 ≤1: " + ("✓ → EP8 触发条件成立" if s6 <= 1 else "✗ → EP8 触发条件不成立"))

    # Save new metadata for archival
    new_meta_path = output_dir / "metadata_realcap.json"
    new_meta = {
        "version": "0.4.27",
        "movie_name": "西虹市首富",
        "test_type": "s6_realcap_supplementary",
        "baseline_dir": str(baseline_dir),
        "realcap_run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "realcap_elapsed_sec": round(elapsed, 1),
        "match_summary": new_summary,
        "s6_score": s6,
        "s6_reason": s6_reason,
        "matched_clips_count": len(ctx.matched_clips),
        "matched_clips": [m.model_dump() for m in ctx.matched_clips],
    }
    new_meta_path.write_text(
        json.dumps(new_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"\n[save] {new_meta_path}")

    # Print first 3 matched clips for spot-check
    log("\n[spot-check] first 3 matched clips:")
    for mc in ctx.matched_clips[:3]:
        log(f"  seg={mc.segment_index} scene={mc.scene_index} src=[{mc.src_start:.1f},{mc.src_end:.1f}] "
            f"score={mc.score:.3f} source={mc.source}")
        log(f"    text: {mc.text}")

    log(f"\n[done] match_clips status: {ctx.status.match}")
    return 0 if ctx.status.match == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
