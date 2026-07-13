from typing import Any, Dict, Optional, Protocol


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
