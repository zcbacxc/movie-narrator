"""WebSocket endpoint for real-time progress streaming."""

from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from .tasks import TaskManager


async def task_ws_endpoint(websocket: WebSocket, task_id: str, manager: TaskManager) -> None:
    """WebSocket handler: push console snapshots to connected clients."""
    await websocket.accept()

    info = manager.get_task(task_id)
    if not info:
        await websocket.send_json({"type": "terminal", "status": "failed", "error": "Task not found"})
        await websocket.close()
        return

    last_version = -1

    try:
        while True:
            # Check for client messages (subscribe/cancel)
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.2)
                data = json.loads(msg)
                if data.get("action") == "cancel":
                    manager.cancel_task(task_id)
            except asyncio.TimeoutError:
                pass

            # Push snapshot if version changed
            version, text, step = info.console.snapshot()
            if version != last_version:
                last_version = version
                await websocket.send_json({
                    "type": "progress",
                    "step": step,
                    "version": version,
                    "log": text,
                })

            # Check terminal state
            if info.status in ("done", "failed", "cancelled"):
                status_dict = info.to_status_dict()
                await websocket.send_json({
                    "type": "terminal",
                    "status": info.status,
                    "error": status_dict.get("error"),
                    "artifacts": status_dict.get("artifacts", []),
                    "video_path": status_dict.get("video_path"),
                })
                break

    except WebSocketDisconnect:
        pass
