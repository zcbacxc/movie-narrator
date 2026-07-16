"""TaskManager — create/query/cancel pipeline tasks.

Single-task at a time (ThreadPoolExecutor max_workers=1). Each task
runs build_context + run_pipeline in a background thread, with a
WebSocketConsole and TaskController for progress streaming and cancel.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional

from ..utils.sanitize import sanitize_filename
from .console import WebSocketConsole
from .controller import TaskController
from .form import form_to_context_args, validate_form
from .models import TaskCreateRequest
from .utils import collect_artifacts


class TaskInfo:
    """Runtime state for a single task."""

    def __init__(self, task_id: str, output_dir: Path) -> None:
        self.task_id = task_id
        self.output_dir = output_dir
        self.console = WebSocketConsole()
        self.controller = TaskController()
        self.status: str = "running"  # running | done | failed | cancelled
        self.current_step: str = ""
        self.error: Optional[str] = None
        self.artifacts: list[str] = []
        self.video_path: Optional[str] = None
        self._lock = threading.Lock()

    def set_terminal(self, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            self.status = status
            self.error = error

    def to_status_dict(self) -> dict:
        with self._lock:
            return {
                "task_id": self.task_id,
                "status": self.status,
                "current_step": self.current_step,
                "error": self.error,
                "artifacts": self.artifacts,
                "video_path": self.video_path,
            }


class TaskManager:
    """Manages pipeline tasks — single concurrent task."""

    def __init__(self, base_output_dir: str = "output") -> None:
        self._tasks: Dict[str, TaskInfo] = {}
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._base_output_dir = Path(base_output_dir)
        self._lock = threading.Lock()

    def create_task(
        self,
        request: TaskCreateRequest,
        video_path: Optional[str] = None,
        bgm_path: Optional[str] = None,
    ) -> str:
        """Create and start a new task. Returns task_id."""
        # Validate form
        form_data = request.to_form_data(video_path=video_path, bgm_path=bgm_path)
        errors = validate_form(form_data)
        if errors:
            raise ValueError("; ".join(errors))

        task_id = uuid.uuid4().hex[:12]
        output_dir = self._base_output_dir / sanitize_filename(request.movie) / task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        info = TaskInfo(task_id, output_dir)
        with self._lock:
            self._tasks[task_id] = info

        # Submit to thread pool
        self._executor.submit(self._run_task, task_id, form_data, output_dir)
        return task_id

    def _run_task(self, task_id: str, form_data, output_dir: Path) -> None:
        """Run the pipeline in a background thread."""
        info = self._tasks[task_id]
        info.controller.reset()
        info.console.clear()

        try:
            from ..pipeline.runner import build_context, run_pipeline

            kwargs = form_to_context_args(form_data)
            kwargs["output_dir"] = str(output_dir)
            kwargs["services"] = None  # will be injected by build_context

            ctx = build_context(**kwargs)
            ctx.services.console = info.console

            # Controller is passed to run_pipeline as a kwarg (matching the
            # CLI / Gradio pattern in pipeline/runner.py). Context has no
            # ``controller`` field, so it cannot be stored on ctx.
            run_pipeline(ctx, controller=info.controller)

            # Collect artifacts
            info.artifacts = collect_artifacts(ctx, output_dir)
            info.video_path = str(ctx.video_path) if ctx.video_path else None
            info.set_terminal("done")

        except Exception as e:
            import traceback
            info.set_terminal("failed", f"{e}\n{traceback.format_exc()}")

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        info = self.get_task(task_id)
        if info and info.status == "running":
            info.controller.cancel()
            return True
        return False

    def update_step(self, task_id: str, step: str) -> None:
        """Called by pipeline runner to update current step."""
        info = self.get_task(task_id)
        if info:
            with info._lock:
                info.current_step = step
