# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`movie-narrator` — a Python CLI (`mn`) that turns one prompt into a narrated movie-recap video. Pipeline: **Resolve → Assets → Research → Script → Script Export → TTS → Align → Scenes → Match → BGM → Translate → Subtitle → QA Gate → Render → QA → Export Clips** (16 steps), orchestrated over a shared mutable `Context` model. Targets Python 3.10+. Current version: **0.8.0** (CONTRACT_VERSION `(0, 8, 0)`).

For full architecture details, read `docs/ARCHITECTURE.md` and `docs/METADATA_SCHEMA.md`.

## Common Commands

```bash
# Install (dev mode with test deps)
pip install -e ".[dev]"

# Run unit tests (fast, no network or LLM needed)
pytest -v

# Single test
pytest tests/test_context.py::test_format_time -v

# Generate a video (requires LLM endpoint reachable; defaults to local Ollama)
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60

# Use a job YAML config file
mn create --config examples/job.example.yaml

# CI-mode run (uses silent fallback audio, no Edge-TTS network calls)
CI=1 mn create --movie "CI-Test" --style "热血搞笑" --duration 10 --keep-cache

# Task queue commands (v0.6+)
mn submit -m "飞驰人生" -p douyin-fast    # submit async task
mn status <task_id>                        # show task status
mn serve --port 8765 --max-workers 2       # start daemon + REST API server

# Pipeline control
mn create --movie "飞驰人生" --pause-at script  # pause after script generation
mn resume --state output/<movie>/pipeline_state.json  # resume from checkpoint

# Plugin commands (v0.5+)
mn plugin list          # list discovered entry_points
mn plugin version       # show CONTRACT_VERSION
```

External prerequisites: **FFmpeg** on `PATH`. The renderer locates it via `shutil.which`.

Optional extras: `[media]` (scene detection), `[ml]` (WhisperX alignment, embedding matching), `[full]` (all extras), `[docs]` (mkdocs), `[dev]` (pytest).

## Architecture (Big Picture)

The pipeline is a **flat sequence of pure functions** `step(ctx: Context) -> Context` defined in `src/movie_narrator/pipeline/runner.py:STEPS`. Each step reads from and writes back to the shared `Context` (defined in `models.py`). The step list is managed by `StepRegistry` (`pipeline/registry.py`) — built-in steps are registered at import time, and external plugins can inject steps via `@register_step` with `after=`/`before=` ordering hints.

Eight steps are **soft** (optional): `research_plot`, `align_audio`, `detect_scenes`, `match_clips`, `mix_bgm`, `translate_subtitles`, `run_qa_gate`, `export_clips`. Each writes its outcome to `ctx.status.<field>` as one of `disabled` / `skipped` / `success` / `failed`. Pass `--strict` to make soft failures fatal.

For module responsibilities, data model fields, and configuration tables, see `docs/ARCHITECTURE.md`. For metadata field reference, see `docs/METADATA_SCHEMA.md`.

## Configuration

`Settings` (pydantic-settings) loads from `.env` (project root) and `~/.movie-narrator/.env` (user global), with `MN_` env prefix. **Boundary**: `.env` (Settings) = LLM + TTS credentials, endpoints, models, and call params only. All pipeline behavior (scene, match, render, etc.) is configured via `job.yaml` params — see `examples/job.example.yaml` for defaults and `.env.example` for all settings fields.

Priority: **CLI flags > YAML config > Settings defaults**.

## Extension Points

- **New pipeline stage**: register via `@register_step("name", after="step_x")` from `movie_narrator`. Signature: `(ctx: Context) -> Context`. Add to `SOFT_STATUS_STEPS` if skippable. Add a field to `PipelineStatus` in `models.py`.
- **Swap TTS/Vision/LLM/Research provider**: register via `@register_tts("name")` / `@register_vision("name")` / `@register_llm("name")` / `@register_research("name")` from `movie_narrator`. Factory must return an instance satisfying the corresponding ABC.
- **Full plugin (out-of-tree)**: create a package with `[project.entry-points."movie_narrator.plugins"]` in `pyproject.toml`, implement the `Plugin` protocol (`name` + `register(ctx: PluginContext)`). See `examples/plugins/` for templates. See `docs/PLUGIN_DEVELOPMENT.md` for the authoritative entry-point snippet.
- **Add YAML config field**: add to `JobConfig` schema in `workflow/schema.py`, add to `_ALLOWED_TOP` in `workflow/load.py`, handle in `merge_job()` in `workflow/merge.py`.

## Test Strategy

`tests/` has 69 test files covering unit tests, pipeline step tests (with mocks for external deps), CLI integration tests, runner tests (strict mode, soft step status, pause/resume, retry), plugin & contract tests, TTS provider tests, and an E2E smoke test.

Heavy-path tests (LLM, Edge-TTS, MoviePy render) aren't run in unit tests — the CI workflow runs them as a **smoke test** with `CI=1` to force silent-audio fallback so the pipeline is exercised end-to-end without network. New pipeline steps should follow this pattern: pure logic unit-tested, external integration covered via the smoke job.

## Key Gotchas

### Web UI — separate repo

The FastAPI + React SPA lives in the external repository [`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web). This core repo has **no** `webui/` or `web_api/` directory, no `mn web` command, and no `[web]` extra. `fastapi` / `uvicorn` / `python-multipart` are **not** dependencies. When modifying the contract surface, keep `contract.py` and `tests/test_contract.py` in sync so the external web package keeps compiling against the core engine.

### Licensing (PEP 639)

- **SPDX Headers**: All source files under `src/movie_narrator/` must start with `# SPDX-FileCopyrightText: 2026 zcbacxc` and `# SPDX-License-Identifier: AGPL-3.0-or-later`. Test files are optional.
- **pyproject.toml**: Use PEP 639 string format (`license = "AGPL-3.0-or-later"`), not the PEP 621 table format. Build system requires `setuptools>=77.0`.
- **Trove classifier**: Do NOT include license trove classifiers — PEP 639 makes them mutually exclusive with the `license` string expression.
- **Deprecated identifiers**: `AGPL-3.0` (without `-only` or `-or-later`) is deprecated in SPDX 3.0+; always use `AGPL-3.0-or-later`.

### Windows Environment

- Do NOT use `export VAR=value command` syntax on Windows; use native PowerShell commands.
- Filename sanitizer handles Windows-reserved names (`CON`, `PRN`, `COM1`…) and `<>:"/\|?*` characters.
- CJK font fallback chain: `assets/fonts/NotoSansSC-Regular.otf` → system paths → raises install hint.

## Conventions

- Commit message prefixes per `docs/CONTRIBUTING.md`: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`.
- Output structure: `output/<sanitized_movie>/` with `cache/` dir (deleted unless `--keep-cache`). Generated artifacts (`*.mp4`, `*.mp3`, `*.srt`, `*.json`, `output/`) are gitignored.
- Pipeline prints `▶` on entry / `✓` on success (with elapsed time) / `✗ <err>` on failure. Soft steps show `⏭` for disabled/skipped.

## Path-Scoped Rules

Additional rules in `.claude/rules/` load on demand when working with matching files:

- `documentation.md` — Documentation standards for `docs/**/*.md`, `README.md`, `CHANGELOG.md`
- `release.md` — PyPI release workflow for `pyproject.toml`, `CHANGELOG.md`, `publish.yml`
- `git-workflow.md` — Git branch model and commit conventions for `**/*.py`, CI workflows
