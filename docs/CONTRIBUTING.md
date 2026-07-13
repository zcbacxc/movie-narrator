# Contributing

## Development Setup

```bash
git clone https://github.com/zcbacxc/movie-narrator.git
cd movie-narrator
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest -v
```

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
