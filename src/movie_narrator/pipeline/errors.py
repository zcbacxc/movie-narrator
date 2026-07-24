from enum import Enum
from typing import Any, Dict, Optional, Protocol


class StepAction(Enum):
    """User's choice when a hard step fails and interactive retry is enabled.

    - ``RETRY``: re-run the step (ctx state is preserved, so cached
      partial results like TTS segments are reused).
    - ``SKIP``: mark the step as skipped/warning and continue to the
      next step.  Downstream steps may fail if they depend on the
      skipped step's output.
    - ``ABORT``: stop the pipeline (re-raise the original exception).
    """

    RETRY = "retry"
    SKIP = "skip"
    ABORT = "abort"


class PipelineStrictError(RuntimeError):
    def __init__(
        self,
        step: Optional[str],
        status: Dict[str, Any],
        message: Optional[str] = None,
    ):
        self.step = step
        self.status = status
        msg = message or f"strict mode: step failed ({step})"
        super().__init__(msg)


class PipelineCancelled(RuntimeError):
    """Cooperative cancel requested by RunController.

    Raised at step boundaries by ``check_cancelled``. Callers **must**
    catch this before bare ``except Exception`` — otherwise it surfaces
    as a generic ``RuntimeError`` with traceback.

    Cancel is a distinct terminal path: it is NOT a soft-step warning
    and does NOT trip ``--strict``.
    """


class PipelinePaused(RuntimeError):
    """Pipeline paused at a step boundary for human-in-the-loop (EP9).

    Raised by ``run_pipeline`` when ``ctx.metadata["pause_at"]`` matches
    the step that just completed. The pipeline state is serialized to
    ``pipeline_state.json`` in the output directory before raising.

    Callers **must** catch this before bare ``except Exception``.
    ``mn resume`` deserializes the state and re-enters ``run_pipeline``
    starting from the next step.

    Attributes:
        completed_step: name of the step that just finished.
    """

    def __init__(self, completed_step: str):
        self.completed_step = completed_step
        super().__init__(f"pipeline paused after step: {completed_step}")


class RunController(Protocol):
    """Protocol for cooperative pipeline cancellation.

    The pipeline thread calls ``is_cancelled()`` at step boundaries.
    The UI thread (or a future CLI signal handler) calls ``cancel()``
    to set the flag. ``controller=None`` means CLI mode — no cancel
    checks fire.
    """

    def is_cancelled(self) -> bool: ...


def check_cancelled(controller: Optional[RunController]) -> None:
    """Raise ``PipelineCancelled`` if *controller* says cancel was requested."""
    if controller is not None and controller.is_cancelled():
        raise PipelineCancelled()
