from ..models import Context, StepResult
from ..utils.optional_deps import probe


def align_audio(ctx: Context) -> Context:
    """Align timed segments using WhisperX transcription + forced alignment.

    AQ-01 fix (Q-X11): Now runs the full WhisperX pipeline
    (transcribe → align) instead of only midpoint remapping.
    Adds monotonic/non-overlap validation and drift detection.
    Degrades to ``status='skipped'`` when ASR is empty or unreliable.

    Parameters
    ----------
    ctx : Context
        Mutable pipeline context; ``ctx.timed_segments`` is updated
        in-place with WhisperX-derived start/end times when successful.

    Returns
    -------
    Context
        The same (mutated) context with ``ctx.status.align`` set to one
        of ``disabled`` / ``skipped`` / ``success`` / ``failed``.
    """
    ok, hint = probe("whisperx")
    if not ok:
        ctx.status.align = "disabled"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = hint
        return ctx
    if not ctx.audio_path:
        ctx.status.align = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no audio"
        return ctx

    try:
        import whisperx

        device = ctx.metadata.get("whisperx_device", "cpu")
        language = ctx.metadata.get("whisperx_language", "zh")
        audio = whisperx.load_audio(ctx.audio_path)
        model = whisperx.load_model(
            ctx.metadata.get("whisperx_model", "medium"), device=device
        )
        result = model.transcribe(audio, language=language)

        if not result or "segments" not in result or not result["segments"]:
            # ── AQ-01: empty ASR → skipped (not success) ──
            ctx.services.console.inline_warn(
                "WhisperX returned no speech segments; "
                "timestamps remain TTS-estimated"
            )
            ctx.status.align = "skipped"
            ctx.step_state.result = StepResult.WARNING
            ctx.step_state.message = "WhisperX ASR returned empty"
            ctx.metadata["align_degraded"] = True
            return ctx

        # ── AQ-01: Run full forced alignment (Q-X11) ──
        # Previously only midpoint remapping was done. Now we run
        # whisperx.align() for word-level timestamps, then validate.
        try:
            model_a, metadata = whisperx.load_align_model(
                language_code=language, device=device
            )
            result = whisperx.align(
                result["segments"], model_a, metadata, audio, device=device
            )
        except Exception as align_err:
            ctx.services.console.inline_warn(
                f"WhisperX forced alignment failed ({align_err}); "
                f"falling back to transcript-level timestamps"
            )
            ctx.metadata["align_fallback"] = True
            # C1 fix: mark as 'failed' (vs previous secret 'success') so
            # users, CLI summary and metadata.json all see the alignment
            # degradation. Remapping still runs below (segment-level
            # timestamps from transcribe are better than TTS estimates),
            # but the degradation is visible.
            #
            # F3 (runner.py): the runner now also accumulates
            # _degraded_steps for soft steps that return normally with
            # status='failed' + step_state.result=WARNING, so this
            # fallback is surfaced in the runner's degradation summary
            # too (not just metadata.status.align).
            ctx.status.align = "failed"
            ctx.step_state.result = StepResult.WARNING
            ctx.step_state.message = f"forced alignment failed: {align_err}"
            ctx.metadata["align_degraded"] = True

        wx_segments = []
        for wseg in result.get("segments", []):
            start = wseg.get("start", 0.0)
            end = wseg.get("end", 0.0)
            text = wseg.get("text", "").strip()
            if text:
                wx_segments.append({"start": start, "end": end, "text": text})

        if not wx_segments:
            ctx.services.console.inline_warn(
                "WhisperX alignment produced no usable segments; "
                "timestamps remain TTS-estimated"
            )
            ctx.status.align = "skipped"
            ctx.step_state.result = StepResult.WARNING
            ctx.step_state.message = "WhisperX align produced no segments"
            ctx.metadata["align_degraded"] = True
            return ctx

        # ── AQ-01: Drift detection ──
        # If WhisperX finds only 1 segment for the entire audio, it's
        # unreliable (likely all-silence or all-noise detected as one blob).
        if len(wx_segments) == 1 and ctx.timed_segments:
            total_narr_duration = sum(
                ts.end - ts.start for ts in ctx.timed_segments
            )
            wx_duration = wx_segments[0]["end"] - wx_segments[0]["start"]
            if total_narr_duration > 0:
                drift_ratio = abs(wx_duration - total_narr_duration) / total_narr_duration
                if drift_ratio > 0.5:
                    ctx.services.console.inline_warn(
                        f"WhisperX single-segment duration drift {drift_ratio:.0%} "
                        f"(wx={wx_duration:.1f}s vs narr={total_narr_duration:.1f}s); "
                        f"timestamps remain TTS-estimated"
                    )
                    ctx.status.align = "skipped"
                    ctx.step_state.result = StepResult.WARNING
                    ctx.step_state.message = "WhisperX drift too large"
                    ctx.metadata["align_degraded"] = True
                    return ctx

        # ── AQ-01: Monotonic non-overlap remapping ──
        # Match each narration segment to the WhisperX segment whose
        # time range contains the narration midpoint. Validate that
        # resulting timestamps are monotonically non-decreasing and
        # non-overlapping.
        prev_end = 0.0
        for i, ts in enumerate(ctx.timed_segments):
            ts_mid = (ts.start + ts.end) / 2.0
            best = None
            best_dist = float("inf")
            for wseg in wx_segments:
                if wseg["start"] <= ts_mid <= wseg["end"]:
                    best = wseg
                    break
                wseg_mid = (wseg["start"] + wseg["end"]) / 2.0
                dist = abs(wseg_mid - ts_mid)
                if dist < best_dist:
                    best_dist = dist
                    best = wseg
            if best:
                new_start = best["start"]
                new_end = best["end"]
                # Enforce monotonic non-overlap: if new_start < prev_end,
                # push it forward to prev_end (with tiny gap).
                if new_start < prev_end:
                    new_start = prev_end
                if new_end <= new_start:
                    # Floor = 100ms: avoids zero-duration segments (which
                    # would break SRT generation and downstream timing),
                    # while staying short enough to not skew QA's
                    # duration ratio (which compares final.mp4 total
                    # length vs expected, not per-segment lengths).
                    # 100ms is the minimum audible word duration in
                    # natural speech; anything shorter is perceptually
                    # a click, not a word.
                    new_end = new_start + 0.1  # minimum 100ms segment
                ts.start = new_start
                ts.end = new_end
                prev_end = new_end

        # C1 fix: only mark success if forced alignment didn't fall back.
        # Fallback path keeps status='failed' (set in except block above)
        # so runner's _degraded_steps accumulates and metadata exposes it.
        # Remapping still runs on fallback (segment-level timestamps from
        # transcribe are better than TTS estimates), but users see the
        # degradation signal.
        if not ctx.metadata.get("align_fallback"):
            ctx.status.align = "success"
        ctx.metadata["align_segments"] = len(wx_segments)
        return ctx
    except Exception as e:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        ctx.status.align = "failed"
        return ctx
