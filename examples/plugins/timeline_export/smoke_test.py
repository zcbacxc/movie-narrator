# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Manual smoke test for the timeline_export plugin.

Runs against the source tree (not the installed package). Builds a mock
``Context`` with realistic matched clips / timed segments / render_template
and exercises the full step flow for the ``jianying`` backend. For the
``otio`` backend it verifies the soft-disable path when ``opentimelineio``
is missing (the normal Post/PRE-3.14 environment), and the write path when
it is available.

Run from the repo root::

    python examples/plugins/timeline_export/smoke_test.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
PLUGIN_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(PLUGIN_DIR))

from movie_narrator import Context  # noqa: E402
from movie_narrator.models import MatchedClip, TimedSegment  # noqa: E402

from movie_narrator_timeline_export.plugin import (  # noqa: E402
    _build_timeline,
    _probe_otio,
    _timeline_export_step,
    _write_jianying,
    _write_otio,
)

# ── Mock data ────────────────────────────────────────────────────────


def make_context(out_dir: str) -> Context:
    ctx = Context(
        movie_name="飞驰人生",
        style="热血搞笑",
        duration=60,
        output_dir=out_dir,
        source_video_path=r"C:\mock\source.mp4",
    )
    # Three matched clips (video track) with narration + source times.
    ctx.matched_clips = [
        MatchedClip(
            segment_index=0,
            text="开场张驰落魄",
            narr_start=0.0,
            narr_end=2.5,
            src_start=10.0,
            src_end=12.5,
            score=0.91,
            source="embedding",
        ),
        MatchedClip(
            segment_index=1,
            text="赛道疾驰高潮",
            narr_start=2.5,
            narr_end=5.0,
            src_start=120.0,
            src_end=123.0,
            score=0.88,
            source="heuristic",
        ),
        MatchedClip(
            segment_index=2,
            text="终点夺冠",
            narr_start=5.0,
            narr_end=8.0,
            src_start=900.0,
            src_end=904.0,
            score=0.95,
            source="embedding_top1",
        ),
    ]
    # Timed segments → subtitle track.
    ctx.timed_segments = [
        TimedSegment(text="今天讲一个赛车手的故事。", start=0.0, end=2.5),
        TimedSegment(text="他曾经跌落谷底。", start=2.5, end=5.0),
        TimedSegment(text="但最终冲过终点。", start=5.0, end=8.0),
    ]
    # Mock render_template (the object under test).
    ctx.metadata["render_template"] = {
        "title_card_text": "{movie} · 飞驰人生解说",
        "end_card_text": "感谢观看 {movie}",
        "watermark_text": "@movie-narrator",
        "disclaimer_text": "本视频仅供学习交流",
        "aspect_safe_area": {"max_width_ratio": 0.9, "bottom_margin_ratio": 0.08},
    }
    return ctx


# ── Assertions / helpers ─────────────────────────────────────────────


def check_jianying(ctx: Context, out_dir: Path) -> None:
    tl = _build_timeline(ctx)
    assert len(tl.clips) == 3, f"expected 3 clips, got {len(tl.clips)}"
    # 3 subtitles + title + end + watermark + disclaimer = 7 overlays.
    assert len(tl.text_overlays) == 7, f"expected 7 overlays, got {len(tl.text_overlays)}"
    assert tl.movie_name == "飞驰人生"

    draft = _write_jianying(tl, out_dir)
    content = json.loads((draft / "draft_content.json").read_text(encoding="utf-8"))
    meta = (draft / "draft_meta_info.json").exists()
    assert meta, "draft_meta_info.json missing"
    timeline = content["draft_timeline"]
    assert len(timeline["videos"][0]["tracks"][0]["segments"]) == 3
    assert len(timeline["texts"]) == 7
    # Materials carry the source path.
    assert content["materials"]["videos"][0]["path"] == r"C:\mock\source.mp4"
    print(f"  ✓ jianying draft written: {draft.name} "
          f"(segments={len(timeline['videos'][0]['tracks'][0]['segments'])}, "
          f"texts={len(timeline['texts'])})")


def check_otio(ctx: Context, out_dir: Path) -> None:
    tl = _build_timeline(ctx)
    if _probe_otio():
        out = _write_otio(tl, out_dir / "otio")
        assert out.suffix == ".otio" and out.exists(), f"otio file missing: {out}"
        print(f"  ✓ otio written: {out.name}")
    else:
        # Soft-disable path: step must return skipped, never raise.
        ctx2 = Context.model_validate(ctx.model_dump())
        ctx2.metadata["timeline_export_backend"] = "otio"
        _timeline_export_step(ctx2)
        assert ctx2.step_state.result.value == "skipped", ctx2.step_state
        assert "opentimelineio" in (ctx2.step_state.message or "")
        print(f"  ✓ otio soft-disabled (opentimelineio missing): {ctx2.step_state.message}")


def check_step_flow(ctx: Context, out_dir: Path) -> None:
    """End-to-end through the registered step (jianying default)."""
    ctx2 = Context.model_validate(ctx.model_dump())
    ctx2.output_dir = str(out_dir)
    ctx2.metadata["timeline_export_backend"] = "jianying"
    _timeline_export_step(ctx2)
    assert ctx2.step_state.result.value == "success", ctx2.step_state
    assert "jianying" in (ctx2.step_state.message or "")
    assert ctx2.metadata.get("timeline_export_path")
    print(f"  ✓ step flow success: {ctx2.step_state.message}")


def main() -> None:
    out_dir = Path(__file__).resolve().parent / "_smoke_out"
    ctx = make_context(str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)

    print("== jianying backend ==")
    check_jianying(ctx, out_dir)
    print("== otio backend ==")
    check_otio(ctx, out_dir)
    print("== full step flow (default jianying) ==")
    check_step_flow(ctx, out_dir)

    print("\nAll timeline_export smoke checks passed.")


if __name__ == "__main__":
    main()
