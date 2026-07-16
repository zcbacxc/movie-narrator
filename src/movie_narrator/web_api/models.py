"""Pydantic models for API request/response validation."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """POST /api/tasks body — JSON form data.

    File uploads (video, bgm) are sent as multipart/form-data alongside
    this JSON in the same request.
    """
    movie: str = Field(..., description="Movie name")
    style: str = Field("热血搞笑", description="Narration style")
    duration: int = Field(60, ge=5, le=600, description="Target duration in seconds")
    voice: str = Field("", description="TTS voice name (empty = Settings default)")
    format: str = Field("16:9", pattern="^(16:9|9:16)$")
    library_dir: str = Field("", description="Library directory for fuzzy video match")
    research: bool = Field(False, description="Enable research step")
    no_bgm: bool = Field(False, description="Disable BGM")
    no_clips: bool = Field(False, description="Skip video clip matching")
    strict: bool = Field(False, description="Strict mode — soft step failures abort")
    subtitle_lang: str = Field("", description="Subtitle language code")
    subtitle_mode: str = Field("original", pattern="^(original|translated|bilingual)$")
    scene_threshold: Optional[float] = Field(None, ge=0, le=100)
    match_min_score: Optional[float] = Field(None, ge=0, le=1)
    translate_provider: str = Field("")
    translate_retries: Optional[int] = Field(None, ge=0, le=10)

    def to_form_data(self, video_path: Optional[str] = None, bgm_path: Optional[str] = None):
        """Convert to FormData for validate_form() and form_to_context_args()."""
        from .form import FormData

        return FormData(
            movie=self.movie,
            style=self.style,
            duration=self.duration,
            voice=self.voice,
            format=self.format,
            video_path=video_path,
            library_dir=self.library_dir,
            research=self.research,
            bgm_path=bgm_path,
            no_bgm=self.no_bgm,
            no_clips=self.no_clips,
            strict=self.strict,
            subtitle_lang=self.subtitle_lang,
            subtitle_mode=self.subtitle_mode,
            scene_threshold=self.scene_threshold,
            match_min_score=self.match_min_score,
            translate_provider=self.translate_provider,
            translate_retries=self.translate_retries,
        )


class TaskCreateResponse(BaseModel):
    """POST /api/tasks response."""
    task_id: str
    status: str = "running"


class TaskStatusResponse(BaseModel):
    """GET /api/tasks/{id} response."""
    task_id: str
    status: str  # running | done | failed | cancelled
    current_step: str = ""
    error: Optional[str] = None
    artifacts: List[str] = []
    video_path: Optional[str] = None


class CancelResponse(BaseModel):
    """DELETE /api/tasks/{id} response."""
    cancelled: bool = True
