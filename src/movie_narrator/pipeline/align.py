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
    if ctx.metadata.get("workflow_steps", {}).get("align") is False:
        ctx.status.align = "disabled"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "disabled by workflow config"
        return ctx
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

        device = "cpu"
        audio = whisperx.load_audio(ctx.audio_path)
        model = whisperx.load_model("medium", device=device)
        result = model.transcribe(audio, language="zh")

        if result and "segments" in result:
            aligned = []
            for wseg in result["segments"]:
                start = wseg.get("start", 0.0)
                end = wseg.get("end", 0.0)
                text = wseg.get("text", "").strip()
                if text:
                    aligned.append({"start": start, "end": end, "text": text})

            for i, ts in enumerate(ctx.timed_segments):
                if i < len(aligned):
                    ts.start = aligned[i]["start"]
                    ts.end = aligned[i]["end"]

        ctx.status.align = "success"
        return ctx
    except Exception as e:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        ctx.status.align = "failed"
        return ctx
