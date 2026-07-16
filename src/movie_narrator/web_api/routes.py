"""REST API routes for task management."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .models import CancelResponse, TaskCreateRequest, TaskCreateResponse, TaskStatusResponse
from .tasks import TaskManager
from .utils import (
    BGM_EXTENSIONS,
    MAX_BGM_SIZE,
    MAX_VIDEO_SIZE,
    VIDEO_EXTENSIONS,
    UploadError,
    save_upload,
    zip_artifacts,
)


def create_router(manager: TaskManager, upload_dir: Path) -> APIRouter:
    """Create the REST API router with all endpoints."""
    router = APIRouter(prefix="/api")

    @router.post("/tasks", response_model=TaskCreateResponse)
    async def create_task(
        movie: str = Form(...),
        style: str = Form("热血搞笑"),
        duration: int = Form(60),
        voice: str = Form(""),
        format: str = Form("16:9"),
        library_dir: str = Form(""),
        research: bool = Form(False),
        no_bgm: bool = Form(False),
        no_clips: bool = Form(False),
        strict: bool = Form(False),
        subtitle_lang: str = Form(""),
        subtitle_mode: str = Form("original"),
        scene_threshold: Optional[float] = Form(None),
        match_min_score: Optional[float] = Form(None),
        translate_provider: str = Form(""),
        translate_retries: Optional[int] = Form(None),
        video: Optional[UploadFile] = File(None),
        bgm: Optional[UploadFile] = File(None),
    ):
        # Save uploaded files with size + extension validation
        video_path = None
        bgm_path = None
        try:
            if video and video.filename:
                video_path = save_upload(
                    video, upload_dir, prefix="video_",
                    max_size=MAX_VIDEO_SIZE,
                    allowed_extensions=VIDEO_EXTENSIONS,
                )
            if bgm and bgm.filename:
                bgm_path = save_upload(
                    bgm, upload_dir, prefix="bgm_",
                    max_size=MAX_BGM_SIZE,
                    allowed_extensions=BGM_EXTENSIONS,
                )
        except UploadError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)

        request = TaskCreateRequest(
            movie=movie, style=style, duration=duration, voice=voice,
            format=format, library_dir=library_dir, research=research,
            no_bgm=no_bgm, no_clips=no_clips, strict=strict,
            subtitle_lang=subtitle_lang, subtitle_mode=subtitle_mode,
            scene_threshold=scene_threshold, match_min_score=match_min_score,
            translate_provider=translate_provider, translate_retries=translate_retries,
        )

        try:
            task_id = manager.create_task(request, video_path=video_path, bgm_path=bgm_path)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        return TaskCreateResponse(task_id=task_id, status="running")

    @router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
    async def get_task(task_id: str):
        info = manager.get_task(task_id)
        if not info:
            raise HTTPException(status_code=404, detail="Task not found")
        return TaskStatusResponse(**info.to_status_dict())

    @router.delete("/tasks/{task_id}", response_model=CancelResponse)
    async def cancel_task(task_id: str):
        cancelled = manager.cancel_task(task_id)
        if not cancelled:
            raise HTTPException(status_code=409, detail="Task not running or not found")
        return CancelResponse(cancelled=True)

    @router.get("/artifacts/{task_id}")
    async def download_artifacts(task_id: str):
        info = manager.get_task(task_id)
        if not info:
            raise HTTPException(status_code=404, detail="Task not found")
        if info.status == "running":
            raise HTTPException(status_code=409, detail="Task still running")

        artifacts = info.artifacts
        if not artifacts:
            raise HTTPException(status_code=404, detail="No artifacts available")

        # If single artifact, return directly
        if len(artifacts) == 1:
            return FileResponse(artifacts[0], filename=Path(artifacts[0]).name)

        # Multiple artifacts → zip
        zip_path = info.output_dir / "artifacts.zip"
        zip_artifacts(artifacts, zip_path)
        return FileResponse(str(zip_path), filename="artifacts.zip")

    @router.get("/video/{task_id}")
    async def download_video(task_id: str):
        """Stream the output video for inline playback."""
        info = manager.get_task(task_id)
        if not info:
            raise HTTPException(status_code=404, detail="Task not found")
        if not info.video_path:
            raise HTTPException(status_code=404, detail="No video available")
        p = Path(info.video_path)
        if not p.exists():
            raise HTTPException(status_code=404, detail="Video file not found")
        return FileResponse(str(p), media_type="video/mp4", filename=p.name)

    return router
