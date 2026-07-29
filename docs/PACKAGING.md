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

- Tracks the core engine version (both at `0.6.x`).
- Declares `movie-narrator>=0.6.0` as dependency.
- Checks `CONTRACT_VERSION >= (0, 6, 0)` at import time via `_MIN_CONTRACT`.

### Third-Party Plugins

- Use independent semver (e.g. `1.0.0`, `0.3.2`).
- Declare `movie-narrator>=X.Y.Z` as dependency.
- Call `check_version()` at import time to enforce minimum contract version:

```python
from movie_narrator.contract import check_version
check_version((0, 6, 1))
```

## Entry Points

Plugins are discovered via the `movie_narrator.plugins` entry point group:

```toml
[project.entry-points."movie_narrator.plugins"]
my-plugin = "my_plugin:MyPluginClass"
```

The entry point must resolve to a class or instance implementing the `Plugin`
protocol (`name` attribute + `register(ctx)` method).

## CLI Plugin Commands

The `mn plugin` command provides introspection:

```bash
mn plugin list          # List installed plugins (entry_points)
mn plugin discover      # Discover and load all plugins
mn plugin registries    # Show all registered steps and providers
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

- **NEVER push directly to `main`** — use `feature/*` or `hotfix/*` branch + PR.
- CI must pass before merge (Python 3.10/3.11/3.12/3.13 test matrix + media).
- Tag push must be executed separately (`git push origin vX.Y.Z`) and not
  combined with branch pushes.
- Feature branches are deleted after merge (both local and remote).
