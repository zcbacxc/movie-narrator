[![English](https://img.shields.io/badge/English-Release_Checklist-blue)](RELEASE_CHECKLIST.md)
[![简体中文](https://img.shields.io/badge/简体中文-发布清单-green)](RELEASE_CHECKLIST.zh-CN.md)

# v1.1 Release Checklist

> **Definition of Done for the v1.1 release.** Every item on this list
> must be verified and checked off before the v1.1.0 tag is created and the
> release is published to PyPI. Items are grouped by category; each has a
> verification command or method.

---

## Code Quality

- [ ] **mypy: zero errors**
  - Command: `mypy src/movie_narrator`
  - Expected: `Success: no issues found in 120 source files`
  - Note: Must pass on Python 3.10 target (as configured in `pyproject.toml`); must match the CI `mypy` invocation exactly

- [ ] **ruff: zero errors**
  - Command: `ruff check src/`
  - Expected: No output (exit code 0)
  - Note: All `E`, `F`, `W`, `BLE`, `A` rules must pass (see `pyproject.toml`); CI lints `src/` only

> **Code formatting (non-blocking, out of scope for v1.0)**: `ruff format` is not enforced by CI or pre-commit (no `.pre-commit-config.yaml` configured). ~80 files under `src/` (150 incl. `tests/`) are currently unformatted. Track as a separate `chore/ruff-format` cleanup so the diff stays isolated from the v1.0 docstring/stability changes.

- [ ] **Test coverage meets threshold**
  - Command: `pytest --cov=movie_narrator --cov-report=term-missing --cov-fail-under=90`
  - Expected: `Required test coverage of 90% reached. Total coverage: XX%`
  - Note: Threshold defined in CI config (`.coveragerc` + `ci.yml`, enforced on the 3.11 matrix leg); must not regress from the v1.0 baseline

---

## Testing

- [ ] **Unit tests: all pass**
  - Command: `pytest -v -m "not integration"`
  - Expected: `XX passed` (0 failed, 0 errors)
  - Note: All tests under `tests/` except those marked `integration`

- [ ] **Integration tests: all pass**
  - Command: `pytest -v -m integration`
  - Expected: All integration tests pass (may be skipped if scenedetect/ffmpeg not available)
  - Note: Requires `scenedetect` (install `[media]` extra); ffmpeg is bundled

- [ ] **E2E smoke test passes**
  - Command: `pytest -v tests/test_e2e_smoke.py`
  - Expected: Test passes with no errors
  - Note: Validates full pipeline execution with minimal inputs

- [ ] **Contract tests pass**
  - Command: `pytest -v tests/test_contract.py`
  - Expected: All contract re-export, protocol, and version tests pass
  - Note: Verifies `CONTRACT_VERSION` value and `__all__` completeness

- [ ] **No flaky tests**
  - Command: `pytest -v --count=3`
  - Expected: Same tests pass consistently across 3 runs
  - Note: Run on CI matrix (Python 3.10, 3.11, 3.12, 3.13)

---

## Security

- [ ] **SAST (bandit) passes with zero high/critical findings**
  - Command: `bandit -r src/movie_narrator -c pyproject.toml`
  - Expected: `No issues identified` (or only low/medium with documented exceptions)
  - Note: Excludes tests, examples, docs per `pyproject.toml` bandit config

- [ ] **Dependency audit (pip-audit) passes**
  - Command: `pip-audit`
  - Expected: `No known vulnerabilities found`
  - Note: Run in a clean `pip install -e ".[dev]"` environment
  - Note: Documented ignore-list entries (e.g., pillow 11.x) must be re-evaluated

- [ ] **No hardcoded secrets in code**
  - Method: Manual review + CI secret scanning (GitHub secret scanning)
  - Expected: No API keys, tokens, or credentials committed to source
  - Note: Verify with `git diff main --name-only | xargs grep -l "sk-\|api_key\|secret"`

- [ ] **SECURITY.md is up to date**
  - Verification: Review `SECURITY.md` and `SECURITY.zh-CN.md`
  - Expected: Vulnerability reporting process is current, contact info is valid

- [ ] **ADR-011 forbidden dependency check passes (machine-enforceable subset)**
  - Command: `python scripts/check_forbidden_deps.py`
  - Expected: No forbidden pip-installable packages found (Remotion, TypeTale, yt-dlp, Bilibili API, Playwright, IndexTTS, CosyVoice)
  - Note: This checks the machine-detectable subset of ADR-011's red-line list (see `docs/ADR.md` ADR-011 and `docs/CONTRIBUTING.md` License Red Lines). Non-package red lines (e.g. copying TypeTale source, using scrapers behaviorally) still require manual code review.

- [ ] **FFmpeg bundle check passes**
  - Command: `python scripts/check_no_ffmpeg_bundle.py`
  - Expected: No `ffmpeg` or `ffprobe` binary found in built wheel
  - Note: Confirms the ADR-011 FFmpeg policy; also runs automatically in `.github/workflows/publish.yml` after `twine check`.

---

## Documentation

- [ ] **All bilingual docs are structurally aligned**
  - Method: Compare EN and ZH versions of each document pair
  - Expected: Same chapter count, same section hierarchy, same tables
  - Files to verify: `README`, `ARCHITECTURE`, `ROADMAP`, `CONTRIBUTING`,
    `BEST_PRACTICES`, `LLM_PROVIDERS`, `METADATA_SCHEMA`, `PACKAGING`,
    `PLUGIN_DEVELOPMENT`, `QUICKSTART`, `AI_GUIDE`, `ADR`, `MIGRATION`,
    `TUTORIAL`, `DEPLOYMENT`, `OBSERVABILITY`, `STABILITY`, `RELEASE_CHECKLIST`

- [ ] **Migration Guide is complete and reviewed**
  - Verification: Read `docs/MIGRATION.md` end-to-end
  - Expected:
    - v0.x → v1.0 upgrade steps are clear and accurate
    - All breaking changes are documented
    - Rollback procedure is included
    - FAQ covers common upgrade scenarios

- [ ] **API Reference (SDK docs) is complete**
  - Verification: Run `mkdocs build` and inspect SDK reference pages
  - Expected: All `movie_narrator.contract` exports are documented
  - Note: `docs/sdk/` pages list all modules: contract, models, pipeline,
    step_registry, errors, registries, tts, vision, presets, cloud, reliability

- [ ] **Stability document is published**
  - Verification: `docs/STABILITY.md` and `docs/STABILITY.zh-CN.md` exist and are linked from `mkdocs.yml` nav
  - Expected: API stability promise, versioning policy, deprecation policy,
    upgrade guarantees, Python version support, contract compatibility matrix

- [ ] **CHANGELOG.md is finalized**
  - Verification: Review `CHANGELOG.md`
  - Expected:
    - `[Unreleased]` section moved to `[1.1.0]`
    - All Keep a Changelog categories present (Added, Changed, Deprecated, Removed, Fixed, Security)
    - `CONTRACT_VERSION` line remains `(1, 0, 0)` (no new contract exports in v1.1)
    - Version comparison links at bottom are complete

- [ ] **mkdocs build succeeds**
  - Command: `mkdocs build`
  - Expected: Build completes with no warnings or errors
  - Note: All navigation links resolve, all images load, all code blocks render

---

## Release Preparation

- [ ] **Version numbers are aligned**
  - Verification:
    - `pyproject.toml` → `version = "1.1.0"`
    - `src/movie_narrator/contract.py` → `CONTRACT_VERSION = (1, 0, 0)` (unchanged)
    - `docs/ROADMAP.md` → CONTRACT_VERSION line shows `(1, 0, 0)` (unchanged in v1.1)
    - `docs/MIGRATION.md` → current/target version references updated
  - Expected: Package version 1.1.0; contract version remains (1, 0, 0)

- [ ] **Tag naming follows convention**
  - Format: `v1.1.0` (lowercase `v`, semver, no prefix/suffix)
  - Command: `git tag -a v1.1.0 -m "v1.1.0 - Community & Polish"`
  - Note: Annotated tag, not lightweight

- [ ] **Release branch is merged to main**
  - Verification: `release/v1.1` branch is merged into `main` via PR
  - Expected: All CI checks pass on the merge commit
  - Note: No direct pushes to `main`

- [ ] **PyPI publish workflow is ready**
  - Verification: `.github/workflows/publish.yml` exists and is configured
  - Expected: Trusted Publisher configured, tag push triggers publish
  - Manual verification:
    ```bash
    python -m build
    twine check dist/*
    pip install dist/movie_narrator-1.1.0-py3-none-any.whl
    mn version  # should show 1.1.0
    ```

- [ ] **PyPI release verified**
  - Verification:
    ```bash
    pip install movie-narrator==1.1.0
    python -c "from movie_narrator.contract import CONTRACT_VERSION; print(CONTRACT_VERSION)"
    # Expected: (1, 0, 0)
    ```
  - Expected: Package installs cleanly, import works, package version 1.1.0

- [ ] **Git tag pushed**
  - Command: `git push origin v1.1.0`
  - Expected: Tag appears on GitHub, publish workflow starts
  - Note: Push tag only after all checklist items are confirmed

- [ ] **GitHub Release created**
  - Verification: Release page created on GitHub with tag `v1.1.0`
  - Expected:
    - Title: `v1.1.0 - Community & Polish`
    - Body: Summary of key features, links to migration guide and stability doc
    - CHANGELOG entry included
    - Pre-release checkbox is **unchecked**

---

## Post-Release

- [ ] **Announcement posted**
  - Channels: GitHub release page, discussion forum, social media (if applicable)
  - Content: Key features, stability promise, migration guide link

- [ ] **v1.1.x maintenance branch created**
  - Command: `git checkout -b v1.1.x v1.1.0 && git push -u origin v1.1.x`
  - Purpose: Backport security and critical bug fixes for v1.x users

- [ ] **ROADMAP updated for v1.2 planning**
  - Verification: `docs/ROADMAP.md` v1.1.0 moved to Completed table
  - Expected: v1.2.0 planning section added under Current & Planned

---

*Use this checklist during the release candidate (RC) phase. Each RC should
go through the full checklist. The final RC that passes all items becomes
the v1.1.0 release.*
