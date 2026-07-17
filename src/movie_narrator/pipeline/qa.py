"""Hard pipeline step: validate the rendered deliverable.

Runs after ``render_video`` and before ``export_clips``. Probes the output
video and rejects it if it is missing streams, the wrong length, near-silent,
or implausibly small. In CI, the step is skipped unless ``qa_enabled`` is
explicitly set, to keep the smoke test fast and free of binary-probing flakiness.
"""

from __future__ import annotations

from ..models import Context
from ..tts.base import is_ci
from ..utils.deliverable_qa import evaluate_deliverable


def validate_deliverable(ctx: Context) -> Context:
    """Probe the rendered video and fail the pipeline on publishability issues.

    Hard step: a failure raises ``RuntimeError`` so the pipeline stops before
    exporting clips from a broken render. Not registered in
    ``SOFT_STATUS_STEPS`` — failures are real, not advisory.
    """
    # QA gating (spec §7.5):
    #   qa_enabled=True  → always run (even in CI)
    #   qa_enabled=False → always skip (explicit opt-out)
    #   not set (None)   → skip in CI (fast smoke tests), run locally
    qa_enabled = ctx.metadata.get("qa_enabled", None)
    if qa_enabled is False:
        ctx.services.console.debug("QA step skipped (qa_enabled=false)")
        return ctx
    if qa_enabled is None and is_ci():
        ctx.services.console.debug("QA step skipped in CI (qa_enabled not set)")
        return ctx

    video_path = ctx.video_path
    if not video_path:
        raise RuntimeError("QA: no video_path on context — render step did not produce output")

    # Expected duration = last narration segment end (the narration length
    # the render was supposed to cover). Falls back to audio probe length.
    expected = 0.0
    if ctx.timed_segments:
        expected = max(s.end for s in ctx.timed_segments)
    if expected <= 0 and ctx.final_audio_path:
        # Last-resort: derive from audio file via the same probe path.
        from ..utils.deliverable_qa import probe_media

        try:
            expected = probe_media(ctx.final_audio_path).get("duration", 0.0) or 0.0
        except Exception:
            expected = 0.0

    report = evaluate_deliverable(
        video_path,
        expected_duration=expected,
        max_silence_db=ctx.metadata.get("qa_max_silence_db", -50.0),
        min_duration_ratio=ctx.metadata.get("qa_min_duration_ratio", 0.85),
        max_duration_ratio=ctx.metadata.get("qa_max_duration_ratio", 1.25),
    )

    # Stash the report so downstream steps / metadata can surface it.
    ctx.metadata["qa_report"] = {
        "ok": report.ok,
        "issues": [{"code": i.code, "message": i.message} for i in report.issues],
        "metrics": report.metrics,
    }

    if not report.ok:
        codes = ", ".join(i.code for i in report.issues)
        messages = "; ".join(i.message for i in report.issues)
        ctx.services.console.inline_warn(f"QA failed: {codes}")
        raise RuntimeError(f"Deliverable QA failed ({codes}): {messages}")

    ctx.services.console.debug(
        f"QA passed: duration={report.metrics.get('duration', 0):.1f}s, "
        f"vol={report.metrics.get('mean_volume')}dB"
    )
    return ctx
