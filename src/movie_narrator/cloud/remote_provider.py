# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Remote provider proxies and artifact management (v0.6.1).

Provides utilities for:
1. Downloading completed task artifacts (video, audio, subtitles)
   from a remote API server
2. Registering remote LLM/TTS providers that proxy inference calls
   to a remote movie-narrator worker

The artifact download is the primary use case — when a task completes
on a remote worker, the client can fetch the output files.

Typical usage::

    from movie_narrator.cloud import RemoteTaskQueue, download_artifact

    queue = RemoteTaskQueue("http://worker:8765")
    result = queue.wait(task_id, timeout=600)
    if result and result.video_path:
        local_path = download_artifact(
            "http://worker:8765",
            task_id,
            "final.mp4",
            dest_dir="./output",
        )
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Chunk size for streaming downloads
_CHUNK_SIZE = 64 * 1024  # 64 KB


def list_artifacts(
    base_url: str,
    task_id: str,
    *,
    timeout: float = 30.0,
    api_key: Optional[str] = None,
) -> List[Dict[str, str]]:
    """List available output artifacts for a completed task.

    Returns:
        A list of dicts with ``filename``, ``size``, and ``path``
        keys. Returns an empty list if the task has no output directory
        or the directory doesn't exist.
    """
    url = f"{base_url.rstrip('/')}/tasks/{task_id}/artifacts"
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310  # artifact listing from configured remote
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("artifacts", [])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        logger.warning("Failed to list artifacts for task %s: %s", task_id, e)
        return []


def download_artifact(
    base_url: str,
    task_id: str,
    filename: str,
    *,
    dest_dir: Optional[str] = None,
    timeout: float = 300.0,
    api_key: Optional[str] = None,
) -> Path:
    """Download a single artifact file from a remote server.

    Streams the file to disk to handle large video files efficiently.

    Args:
        base_url: Base URL of the remote API server.
        task_id: The task ID.
        filename: Name of the file to download (e.g. ``final.mp4``).
        dest_dir: Destination directory. If None, uses the current
            working directory.
        timeout: Download timeout in seconds.
        api_key: Optional API key for authentication.

    Returns:
        Path to the downloaded file.

    Raises:
        ``RemoteQueueError`` if the download fails.
    """
    from .remote_queue import RemoteQueueError

    url = f"{base_url.rstrip('/')}/tasks/{task_id}/download/{filename}"
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    dest = Path(dest_dir) if dest_dir else Path.cwd()
    dest.mkdir(parents=True, exist_ok=True)
    out_path = dest / filename

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310  # artifact download from configured remote
            with open(out_path, "wb") as f:
                while True:
                    chunk = resp.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
    except urllib.error.HTTPError as e:
        raise RemoteQueueError(
            f"Download failed: HTTP {e.code} {e.reason}"
        ) from e
    except urllib.error.URLError as e:
        raise RemoteQueueError(
            f"Download failed: {e.reason}"
        ) from e

    logger.info("Downloaded %s -> %s (%d bytes)", filename, out_path, out_path.stat().st_size)
    return out_path


def download_all_artifacts(
    base_url: str,
    task_id: str,
    *,
    dest_dir: Optional[str] = None,
    timeout: float = 300.0,
    api_key: Optional[str] = None,
) -> List[Path]:
    """Download all available artifacts for a task.

    Returns:
        A list of paths to downloaded files.
    """
    artifacts = list_artifacts(
        base_url, task_id, timeout=timeout, api_key=api_key
    )
    downloaded: List[Path] = []
    for artifact in artifacts:
        filename = artifact.get("filename", "")
        if not filename:
            continue
        try:
            path = download_artifact(
                base_url, task_id, filename,
                dest_dir=dest_dir, timeout=timeout, api_key=api_key,
            )
            downloaded.append(path)
        except Exception as e:
            logger.warning("Failed to download %s: %s", filename, e)

    return downloaded


# ── Remote LLM/TTS Provider Registration ───────────────────


def register_remote_llm(base_url: str, api_key: Optional[str] = None) -> None:
    """Register a remote LLM provider that proxies to a remote worker.

    This allows the pipeline to offload LLM inference to a remote
    movie-narrator worker by setting ``MN_LLM_PROVIDER=remote``.

    The remote worker must expose an ``/llm/chat`` endpoint that
    accepts OpenAI-compatible chat completion requests.

    Args:
        base_url: Base URL of the remote worker.
        api_key: Optional API key for authentication.
    """
    from ..providers import register_llm
    from ..utils.llm import LLMClient
    from contextlib import contextmanager
    from openai import OpenAI
    import httpx

    @register_llm("remote")
    def _make_remote_llm():
        @contextmanager
        def _cm():
            http_client = httpx.Client(timeout=120.0)
            try:
                client = OpenAI(
                    base_url=f"{base_url.rstrip('/')}/llm",
                    api_key=api_key or "remote",
                    http_client=http_client,
                )
                yield LLMClient(client=client, model="remote")
            finally:
                http_client.close()
        return _cm()

    logger.info("Registered remote LLM provider: %s", base_url)


def register_remote_tts(base_url: str, api_key: Optional[str] = None) -> None:
    """Register a remote TTS provider that proxies synthesis to a remote worker.

    This allows the pipeline to offload TTS synthesis to a remote
    movie-narrator worker by setting ``MN_TTS_PROVIDER=remote``.

    The remote worker must expose a ``/tts/synthesize`` endpoint that
    accepts text and voice parameters and returns audio bytes.

    Args:
        base_url: Base URL of the remote worker.
        api_key: Optional API key for authentication.
    """
    from ..providers import register_tts
    from ..tts.protocol import TTSProvider

    class _RemoteTTSProvider(TTSProvider):
        def __init__(self, url: str, key: Optional[str] = None):
            self._url = url.rstrip("/")
            self._key = key

        async def synthesize(
            self,
            text: str,
            voice: str,
            output_path: Path,
        ) -> None:
            """(async) Synthesize speech from text."""
            import asyncio
            payload = json.dumps({
                "text": text,
                "voice": voice,
            }).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self._key:
                headers["X-API-Key"] = self._key

            req = urllib.request.Request(
                f"{self._url}/tts/synthesize",
                data=payload,
                headers=headers,
                method="POST",
            )
            loop = asyncio.get_event_loop()

            def _fetch():
                with urllib.request.urlopen(req, timeout=60.0) as resp:  # nosec B310  # TTS synthesis from configured remote
                    return resp.read()

            audio_data = await loop.run_in_executor(None, _fetch)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(audio_data)

    @register_tts("remote")
    def _make_remote_tts():
        return _RemoteTTSProvider(base_url, api_key)

    logger.info("Registered remote TTS provider: %s", base_url)
