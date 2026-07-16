# Contributing

## Development Setup

```bash
git clone https://github.com/zcbacxc/movie-narrator.git
cd movie-narrator
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"

# Frontend (for WebUI development)
cd webui && npm install && cd ..
```

## Running Tests

```bash
pytest -v
```

### Frontend verification

When touching anything under `webui/`, verify the SPA still type-checks and builds:

```bash
cd webui && npm run build
```

This runs `tsc` (TypeScript type check) followed by the Vite production build. A clean build is required before the bundle is served by FastAPI.

## Project Structure

```
movie-narrator/
├── src/movie_narrator/
│   ├── pipeline/        # 14-step runner, preflight, tts/render/match/... step modules
│   ├── tts/             # TTS provider abstraction (edge, openai, mimo, factory, cache)
│   ├── web_api/         # FastAPI + WebSocket backend (default WebUI, port 8760)
│   ├── web/             # Legacy Gradio UI (retained for reference, not default)
│   ├── utils/           # llm.py, errors.py, shared helpers
│   ├── models.py        # Context, PipelineStatus, StepState, ...
│   ├── cli.py           # `mn` Typer entry points (create, web, version, ...)
│   └── workflow.py      # job.yaml load/merge (JobConfig, merge_job)
├── webui/               # React 18 SPA — Vite + TypeScript + shadcn/ui + Tailwind
├── tests/               # pytest suite (unit + smoke)
├── docs/                # ARCHITECTURE, ROADMAP, CONTRIBUTING, specs/
└── examples/            # job.example.yaml
```

The WebUI is split across two trees: `src/movie_narrator/web_api/` (Python backend) and `webui/` (React frontend). In production FastAPI serves the Vite-built bundle, so there is no separate frontend server.

## Frontend Development

### Dev mode (two terminals)

During development run the API and the Vite dev server side by side so you get hot-module reloading:

```bash
# Terminal 1 — FastAPI backend (serves API on :8760)
mn web

# Terminal 2 — Vite dev server (HMR; proxies /api and /ws to :8760)
cd webui && npm run dev
```

Open the Vite URL printed in Terminal 2. The Vite dev server proxies REST and WebSocket calls to the FastAPI backend, so the SPA talks to the live API without a manual bundle rebuild on every change.

### Production build

Before shipping frontend changes, rebuild the bundle so FastAPI serves the updated static assets:

```bash
cd webui && npm run build
```

After a successful build, `mn web` alone serves both the API and the freshly built SPA — no second process is needed.

## Code Style

- Follow the existing code style in each module
- Add tests for new pipeline steps
- Update `docs/ROADMAP.md` when adding features

## Commit Convention

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation only
- `chore:` — maintenance, CI, tooling
- `refactor:` — code change that neither fixes a bug nor adds a feature

## Submitting Changes

1. Fork the repo and create a feature branch (`feature/<short-name>` off `main`)
2. Make your changes with tests
3. Run `pytest -v` and ensure all tests pass
4. Update `docs/ROADMAP.md` if you're adding a new feature
5. Add a CHANGELOG entry under `[Unreleased]` (Keep a Changelog format)
6. Submit a pull request targeting `main` (see
   `docs/superpowers/specs/2026-07-13-gitflow-design.md` for the full
   branching model)

## Adding a New Pipeline Step

1. Add a module under `src/movie_narrator/pipeline/` exposing
   `def <step_name>(ctx: Context) -> Context`
2. For soft steps, set `ctx.status.<field>`, `ctx.step_state` (with
   `StepResult.{SKIPPED,WARNING}`) and append to `metadata.warnings` on
   failure — see `pipeline/translate.py` and `pipeline/match.py` for
   the canonical soft-step pattern
3. Register the step in `STEPS`, `SOFT_STATUS_STEPS` (if soft), and
   `STATUS_FIELD_FOR_STEP` in `pipeline/runner.py`
4. Add the status field to `PipelineStatus` in `models.py` (default
   `disabled`, except `translate` which defaults to `skipped`)
5. Add tests under `tests/test_<step>.py` covering the decision matrix
   (disabled / skipped / success / failure) and CLI/YAML integration
