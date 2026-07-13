"""Bridge: form data → background pipeline thread → Gradio generator yields.

The bridge is the glue between the Gradio UI and the pipeline. It:
1. Validates form data.
2. Creates a GradioConsole + GradioController.
3. Starts ``build_context`` + ``run_pipeline`` in a daemon thread.
4. Polls the console every ~200ms and yields log updates to the UI.
5. At terminal state (done / failed / cancelled), collects artifacts.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Generator, Tuple

from ..models import Services
from ..pipeline.errors import PipelineCancelled
from ..pipeline.runner import build_context, run_pipeline
from .console import GradioConsole
from .controller import GradioController
from .form import FormData, form_to_context_args, validate_form
from .models import RunStatus, WebRun
from .utils import collect_artifacts, sanitize_filename


def run_bridge(
    form_data: FormData,
    web_run: WebRun,
) -> Generator[Tuple[Any, ...], None, None]:
    """Generator yielding UI updates for Gradio.

    Each yield is a 6-tuple matching the ``outputs`` list in ``app.py``::
        (status_md, log_md, video_output, files_output, web_run, cancel_btn_update)
    """
    import gradio as gr

    # ── Validate ──────────────────────────────────────────
    errors = validate_form(form_data)
    if errors:
        web_run.status = RunStatus.FAILED
        web_run.error = "\n".join(errors)
        yield (
            "❌ **Validation error:**\n\n" + "\n".join(f"- {e}" for e in errors),
            "",
            None,
            None,
            web_run,
            gr.update(interactive=False),
        )
        return

    # ── Setup ─────────────────────────────────────────────
    console = GradioConsole()
    controller = GradioController()
    controller.reset()

    ctx_args = form_to_context_args(form_data)
    output_dir = Path("output") / sanitize_filename(form_data.movie.strip())
    services = Services(console=console)

    web_run.status = RunStatus.RUNNING
    web_run.controller = controller
    web_run.error = ""
    web_run.current_step = ""
    web_run.output_dir = str(output_dir)

    # ── Build context + run pipeline in background thread ──
    result_holder: dict = {}

    def _worker() -> None:
        try:
            ctx = build_context(
                **ctx_args,
                output_dir=output_dir,
                keep_cache=False,
                services=services,
            )
            web_run.context = ctx
            ctx = run_pipeline(ctx, controller=controller)
            result_holder["ctx"] = ctx
        except PipelineCancelled:
            result_holder["cancelled"] = True
        except Exception as e:
            result_holder["error"] = str(e)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    # ── Poll loop ─────────────────────────────────────────
    last_version = 0
    while thread.is_alive():
        version, log_text, current_step = console.snapshot()
        if version != last_version:
            last_version = version
            web_run.current_step = current_step
            status_text = (
                f"🔄 **Running...** `{current_step}`"
                if current_step
                else "🔄 **Running...**"
            )
            yield (
                status_text,
                log_text,
                None,
                None,
                web_run,
                gr.update(interactive=True),
            )
        time.sleep(0.2)

    thread.join()

    # ── Terminal state ────────────────────────────────────
    _, log_text, _ = console.snapshot()

    if "cancelled" in result_holder:
        web_run.status = RunStatus.CANCELLED
        yield (
            "⊘ **Cancelled** — partial artifacts may be available below.",
            log_text,
            None,
            collect_artifacts(web_run.context),
            web_run,
            gr.update(interactive=False),
        )
        return

    if "error" in result_holder:
        web_run.status = RunStatus.FAILED
        web_run.error = result_holder["error"]
        yield (
            f"❌ **Error:** {result_holder['error']}",
            log_text,
            None,
            collect_artifacts(web_run.context),
            web_run,
            gr.update(interactive=False),
        )
        return

    # ── Success ───────────────────────────────────────────
    ctx = result_holder["ctx"]
    web_run.context = ctx
    web_run.status = RunStatus.DONE
    artifacts = collect_artifacts(ctx)
    video_path = ctx.video_path

    yield (
        f"✅ **Done!** Video: `{video_path}`",
        log_text,
        video_path if video_path and Path(video_path).exists() else None,
        artifacts,
        web_run,
        gr.update(interactive=False),
    )
