[![English](https://img.shields.io/badge/English-Packaging-blue)](PACKAGING.md)
[![简体中文](https://img.shields.io/badge/简体中文-打包-green)](PACKAGING.zh-CN.md)

# Packaging Guide

This document covers packaging conventions for the movie-narrator ecosystem,
including the core engine, the web package, and third-party plugins.

## Versioning

### Core Engine (`movie-narrator`)

- Follows [Semantic Versioning](https://semver.org/) for package version
  (`pyproject.toml` → `version`).
- `CONTRACT_VERSION` (in `contract.py`) is a separate semver tuple that tracks
  the public API surface:
  - **MAJOR** — breaking removals or signature changes to exported symbols
  - **MINOR** — new exports added (backward compatible)
  - **PATCH** — bug fixes / doc changes (no API surface change)
- `CONTRACT_VERSION` and package version are bumped together in the same commit.
- `CHANGELOG.md` must be updated in the same commit as version bumps.

### Web Package (`movie-narrator-web`)

- Uses **independent versioning** — version numbers are NOT aligned with the core engine. Compatibility is determined by `CONTRACT_VERSION` minimum, not by matching package version numbers.
- Declares `movie-narrator>=0.6.0` as dependency.
- Depends exclusively on `movie_narrator.contract` — no internal module imports allowed; the contract layer is the sole API boundary.
- Checks `CONTRACT_VERSION >= _MIN_CONTRACT` at import time.
- `CONTRACT_VERSION` follows semver: only bump MAJOR on breaking removals, MINOR on new exports (backward compatible), PATCH on bug fixes; do NOT bump on every release if the API surface is unchanged.

### Third-Party Plugins

- Use independent semver (e.g. `1.0.0`, `0.3.2`).
- Declare `movie-narrator>=X.Y.Z` as dependency.
- Call `check_version()` at import time to enforce minimum contract version:

```python
from movie_narrator.contract import check_version
check_version((0, 6, 1))
```

## Entry Points

Plugins are discovered via the `movie_narrator.plugins` entry point group.
See [Plugin Development](PLUGIN_DEVELOPMENT.md#entry-points) for the
authoritative entry-point format and examples.

## CLI Plugin Commands

The `mn plugin` command provides introspection. See
[Plugin Development Guide](PLUGIN_DEVELOPMENT.md) for details.

```bash
mn plugin list          # List installed plugins (entry_points)
mn plugin version       # Show CONTRACT_VERSION
```

## Plugin Template

A minimal plugin template is at `examples/plugins/template/`. Copy it to
bootstrap a new plugin — see `examples/plugins/template/README.md` for
instructions.

## Publishing to PyPI

### Core Engine

1. Bump version in `pyproject.toml` + `CONTRACT_VERSION` in `contract.py`.
2. Update `CHANGELOG.md`.
3. Commit: `feat: bump version to X.Y.Z`.
4. Tag: `git tag vX.Y.Z -m "..."` → `git push origin vX.Y.Z`.
5. Tag push triggers the `Publish to PyPI` GitHub Actions workflow
   (uses Trusted Publisher — no API token needed).
6. Verify: `pip install movie-narrator==X.Y.Z`.

### Plugins

1. Follow standard Python packaging: `python -m build` → `twine upload`.
2. Or set up GitHub Actions with Trusted Publisher (recommended).
3. Test installation: `pip install your-plugin && mn plugin list`.

## Git Workflow

See [Contributing Guide](CONTRIBUTING.md) for branch model, PR workflow,
and CI requirements. Key rules:

- **NEVER push directly to `main`** — use `feature/*` or `hotfix/*` branch + PR.
- CI must pass before merge.
- Tag push must be executed separately (`git push origin vX.Y.Z`).
