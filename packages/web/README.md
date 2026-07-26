# movie-narrator-web

Web UI (FastAPI + React) for [movie-narrator](https://github.com/zcbacxc/movie-narrator).

This is a separate package from the core engine. Install it only if you need
the web interface:

```bash
pip install movie-narrator-web
```

This automatically pulls in `movie-narrator` (core engine) and all web
dependencies (FastAPI, uvicorn).

## Usage

```bash
mn-web --host 127.0.0.1 --port 8760
```

Or programmatically:

```python
from movie_narrator_web import launch_web_api

launch_web_api(host="127.0.0.1", port=8760)
```

## Development

The frontend source is in `webui/` (React + Vite + TypeScript + shadcn/ui).
Build the SPA before packaging:

```bash
cd webui
npm ci
npm run build
```

The build output lands in `src/movie_narrator_web/static/` and is served
by FastAPI in production mode.

## Architecture

This package depends on `movie-narrator` exclusively through the public
contract layer (`movie_narrator.contract`). No internal module imports.
