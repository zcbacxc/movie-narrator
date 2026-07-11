from typing import Any, Dict, Optional


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
