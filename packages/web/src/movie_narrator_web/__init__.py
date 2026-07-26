"""Web API — FastAPI + WebSocket backend for the React WebUI."""

from __future__ import annotations

__all__ = ["launch_web_api"]


def launch_web_api(host: str = "127.0.0.1", port: int = 8760, reload: bool = False) -> None:
    """Start the FastAPI web API server.

    Imports are lazy so that ``mn web`` doesn't require fastapi/uvicorn
    unless the user actually launches the web UI.
    """
    import uvicorn

    from .server import create_app

    app = create_app()
    uvicorn.run(app, host=host, port=port, reload=reload)
