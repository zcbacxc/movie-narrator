"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from movie_narrator import __version__

from .routes import create_router
from .tasks import TaskManager
from .ws import task_ws_endpoint


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Movie Narrator Web API", version=__version__)

    # CORS — dev only
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Task manager + upload dir
    upload_dir = Path("output/_uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    manager = TaskManager()

    # Routes
    app.include_router(create_router(manager, upload_dir))

    # WebSocket
    @app.websocket("/ws/task/{task_id}")
    async def ws_endpoint(websocket, task_id: str):
        await task_ws_endpoint(websocket, task_id, manager)

    # Health check
    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    # Serve built frontend (production)
    # Vite builds to this directory (see webui/vite.config.ts build.outDir).
    # When installed via pip, this is included as package data.
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
