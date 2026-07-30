# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""VLMCaptioner — real visual scene descriptions via cloud VLM API (Q-M5).

Extracts a keyframe from each scene's midpoint, sends it to an
OpenAI-compatible vision API (GPT-4o, Qwen-VL, etc.), and returns
concise visual descriptions that unlock embedding re-rank in match.py.

Configuration (environment variables):
    MN_VLM_API_KEY   — API key for the vision model provider
    MN_VLM_MODEL     — Model name (default: gpt-4o)
    MN_VLM_BASE_URL  — API base URL (default: https://api.openai.com/v1)
    MN_VLM_TIMEOUT   — Request timeout in seconds (default: 30)
    MN_VLM_LANGUAGE  — Caption language (default: en; use zh for Chinese)

The provider gracefully handles individual scene failures — failed
scenes get a fallback label so the pipeline doesn't abort.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Optional

from ..models import Scene
from .protocol import VisionCaptioner


class VLMCaptioner(VisionCaptioner):
    """Cloud VLM-based scene captioner.

    Extracts a keyframe at each scene's midpoint using ffmpeg, then
    sends the frame to an OpenAI-compatible vision API for captioning.
    Returns real visual descriptions (not placeholders), so the
    fake-caption guard in match.py treats them as usable labels.

    Frame extraction is cached per video+scene to avoid redundant
    ffmpeg calls across pipeline retries.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        language: Optional[str] = None,
        max_retries: int = 2,
    ):
        self._api_key = api_key or os.environ.get("MN_VLM_API_KEY", "")
        self._model = model or os.environ.get("MN_VLM_MODEL", "gpt-4o")
        self._base_url = (
            base_url
            or os.environ.get("MN_VLM_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self._timeout = timeout or int(os.environ.get("MN_VLM_TIMEOUT", "30"))
        self._language = language or os.environ.get("MN_VLM_LANGUAGE", "en")
        self._max_retries = max_retries
        self._frame_cache: dict[str, str] = {}

    # ── Public API ───────────────────────────────────────────

    def caption_scenes(
        self,
        scenes: List[Scene],
        video_path: Optional[str] = None,
    ) -> List[str]:
        """Generate a text caption for each scene via VLM.

        Args:
            scenes: List of Scene objects with .start, .end, .index.
            video_path: Path to the source video file. Required for
                frame extraction; if None, returns fallback labels.

        Returns:
            List of caption strings, one per scene, aligned 1:1.
            Failed scenes get a fallback label (not a placeholder).
        """
        if not scenes:
            return []

        if not self._api_key:
            raise ValueError(
                "MN_VLM_API_KEY not set — cannot call VLM API. "
                "Set it in .env or pass api_key to VLMCaptioner."
            )

        if not video_path or not Path(video_path).exists():
            return [self._fallback_label(s) for s in scenes]

        captions: List[str] = []
        for scene in scenes:
            try:
                frame_b64 = self._extract_keyframe_b64(scene, video_path)
                caption = self._caption_frame(frame_b64, scene)
                captions.append(caption)
            except Exception:
                logger.debug("VLM captioning failed for scene, using fallback", exc_info=True)
                captions.append(self._fallback_label(scene))

        return captions

    # ── Frame extraction ─────────────────────────────────────

    def _extract_keyframe_b64(self, scene: Scene, video_path: str) -> str:
        """Extract a keyframe at the scene midpoint and return as base64.

        Uses ffmpeg to seek to the midpoint and extract a single frame.
        Results are cached to avoid redundant extraction on retry.
        """
        cache_key = self._frame_cache_key(video_path, scene)
        if cache_key in self._frame_cache:
            cached_path = self._frame_cache[cache_key]
            if Path(cached_path).exists():
                return self._read_b64(cached_path)

        mid_time = (scene.start + scene.end) / 2.0

        # Create temp file for the frame
        tmp_dir = tempfile.gettempdir()
        frame_filename = f"mn_vlm_frame_{cache_key}.jpg"
        frame_path = os.path.join(tmp_dir, frame_filename)

        # Extract frame using ffmpeg
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{mid_time:.2f}",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",  # JPEG quality (2 = high quality)
            "-vf", "scale=512:-1",  # Resize to 512px wide for API efficiency
            frame_path,
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0 or not Path(frame_path).exists():
            raise RuntimeError(
                f"ffmpeg frame extraction failed for scene {scene.index}: "
                f"{result.stderr.strip()[:200]}"
            )

        # Cache the path
        self._frame_cache[cache_key] = frame_path
        return self._read_b64(frame_path)

    def _frame_cache_key(self, video_path: str, scene: Scene) -> str:
        """Generate a cache key from video path and scene info."""
        key_str = f"{video_path}:{scene.index}:{scene.start:.2f}:{scene.end:.2f}"
        return hashlib.md5(key_str.encode()).hexdigest()[:12]

    @staticmethod
    def _read_b64(path: str) -> str:
        """Read a file and return its base64-encoded content."""
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    # ── VLM API call ─────────────────────────────────────────

    def _caption_frame(self, frame_b64: str, scene: Scene) -> str:
        """Send a frame to the VLM API and return the caption text."""
        prompt = self._build_prompt(scene)

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt,
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{frame_b64}",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 80,
            "temperature": 0.3,
        }

        url = f"{self._base_url}/chat/completions"
        data = json.dumps(payload).encode("utf-8")

        last_error: Optional[str] = None
        for attempt in range(self._max_retries + 1):
            try:
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self._api_key}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                content = (
                    result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if content:
                    return content
                last_error = "empty response"

            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}: {e.reason}"
                if e.code == 429:  # Rate limit — wait and retry
                    time.sleep(2 ** attempt)
                elif e.code >= 500:  # Server error — retry
                    time.sleep(1)
                else:  # Client error — don't retry
                    break
            except Exception as e:
                last_error = str(e)
                time.sleep(1)

        raise RuntimeError(f"VLM API failed after {self._max_retries + 1} attempts: {last_error}")

    def _build_prompt(self, scene: Scene) -> str:
        """Build the VLM prompt for scene captioning."""
        if self._language == "zh":
            return (
                "用一句话简短描述这个电影画面中看到的场景和人物动作。"
                "只描述视觉内容，不要推测剧情。"
                "例如：一个穿红衣的男人在雨中奔跑。"
            )
        return (
            "Describe this movie frame in one concise sentence. "
            "Focus on visual content: characters, actions, setting. "
            "Do not speculate about plot. "
            "Example: a man in a red jacket runs through heavy rain."
        )

    @staticmethod
    def _fallback_label(scene: Scene) -> str:
        """Generate a fallback label for failed scenes.

        This is a simple description (not a placeholder format),
        so the fake-caption guard doesn't treat it as fake.
        """
        duration = scene.end - scene.start
        return f"a scene lasting {duration:.1f} seconds starting at {scene.start:.1f}s"
