[![English](https://img.shields.io/badge/English-Contributing-blue)](CONTRIBUTING.md)
[![简体中文](https://img.shields.io/badge/简体中文-贡献指南-green)](CONTRIBUTING.zh-CN.md)

# Contributing

## Development Setup

```bash
git clone https://github.com/zcbacxc/movie-narrator.git
cd movie-narrator
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

> **Web UI is developed in a separate repository.** The FastAPI + React stack now lives in [`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web); this core repo is a pure CLI engine with no `web_api/` or `webui/` directories. To work on the Web UI, clone that repo and follow its own contributing guide.

## Running Tests

```bash
pytest -v
```

## Project Structure

```
movie-narrator/
├── src/movie_narrator/
│   ├── pipeline/        # 15-step runner, preflight, tts/render/match/... step modules
│   ├── tts/             # TTS provider abstraction (edge, openai, mimo, factory, cache)
│   ├── utils/           # llm.py, errors.py, shared helpers
│   ├── models.py        # Context, PipelineStatus, StepState, ...
│   ├── cli.py           # `mn` Typer entry points (create, version, ...)
│   └── workflow.py      # job.yaml load/merge (JobConfig, merge_job)
├── tests/               # pytest suite (unit + smoke)
├── docs/                # ARCHITECTURE, ROADMAP, CONTRIBUTING, specs/
└── examples/            # job.example.yaml
```

The Web UI is developed in a separate repository ([`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web)); it consumes the core engine only through the contract surface defined in `contract.py`. There is no `web_api/` or `webui/` tree in this repo.

## Contributing to the Web UI

The Web UI (FastAPI + React 18 SPA, launched via the standalone `mn-web` command after `pip install movie-narrator-web`) lives in its own repository: [`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web). Frontend and web-backend changes — including `npm install` / `npm run dev` / `npm run build` workflows — belong there, not in this core repo. This repo only maintains the stable `contract.py` API surface that the web package depends on.

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
6. Submit a pull request targeting `main`. This project uses a
   simplified Gitflow: `feature/*` and `hotfix/*` branches merge back to
   `main`; no `release/*` branches are used.

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
