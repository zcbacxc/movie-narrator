from ..models import Context, StepResult
from ..utils.optional_deps import probe


def align_audio(ctx: Context) -> Context:
    """Align timed segments using WhisperX transcription on the rendered audio.

    WhisperX is optional — requires the ``[ml]`` extra or a manual
    ``pip install movie-narrator[ml]``.  When unavailable the step is
    marked *disabled* and skipped so the rest of the pipeline keeps
    running.

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
        audio = whisperx.load_audio(ctx.audio_path)
        model = whisperx.load_model(
            ctx.metadata.get("whisperx_model", "medium"), device=device
        )
        result = model.transcribe(
            audio, language=ctx.metadata.get("whisperx_language", "zh")
        )

        if result and "segments" in result:
            wx_segments = []
            for wseg in result["segments"]:
                start = wseg.get("start", 0.0)
                end = wseg.get("end", 0.0)
                text = wseg.get("text", "").strip()
                if text:
                    wx_segments.append({"start": start, "end": end, "text": text})

            # Match by time overlap instead of index.
            # Index-based alignment causes drift when WhisperX produces
            # a different number of segments than the script (common for
            # long videos with silence or music-only sections).
            for i, ts in enumerate(ctx.timed_segments):
                ts_mid = (ts.start + ts.end) / 2.0
                best = None
                best_dist = float("inf")
                for wseg in wx_segments:
                    # Prefer segments that contain the midpoint
                    if wseg["start"] <= ts_mid <= wseg["end"]:
                        best = wseg
                        break
                    # Otherwise pick the closest by midpoint distance
                    wseg_mid = (wseg["start"] + wseg["end"]) / 2.0
                    dist = abs(wseg_mid - ts_mid)
                    if dist < best_dist:
                        best_dist = dist
                        best = wseg
                if best:
                    ts.start = best["start"]
                    ts.end = best["end"]

        ctx.status.align = "success"
        return ctx
    except Exception as e:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        ctx.status.align = "failed"
        return ctx
