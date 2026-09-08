[![English](https://img.shields.io/badge/English-Release_Checklist-blue)](RELEASE_CHECKLIST.md)
[![简体中文](https://img.shields.io/badge/简体中文-发布清单-green)](RELEASE_CHECKLIST.zh-CN.md)

# v1.6.0 Release Checklist

> **Definition of Done for the v1.6.0 release.** Every item on this list
> must be verified and checked off before the v1.6.0 tag is created and the
> release is published to PyPI. Items are grouped by category; each has a
> verification command or method.

---

## Code Quality

- [x] **mypy: zero errors**
  - Command: `mypy src/movie_narrator`
  - Expected: `Success: no issues found in ... source files`
  - Note: Must pass on Python 3.10 target (as configured in `pyproject.toml`); must match the CI `mypy` invocation exactly

- [x] **ruff: zero errors**
  - Command: `ruff check src/`
  - Expected: No output (exit code 0)
  - Note: All `E`, `F`, `W`, `BLE`, `A` rules must pass (see `pyproject.toml`); CI lints `src/` only

> **Code formatting (non-blocking)**: `ruff format` is not enforced by CI or pre-commit (no `.pre-commit-config.yaml` configured). Tracked as a separate `chore/ruff-format` cleanup so the diff stays isolated from the release changes.

- [x] **Test coverage meets threshold**
  - Command: `pytest --cov=movie_narrator --cov-report=term-missing --cov-fail-under=90`
  - Expected: `Required test coverage of 90% reached. Total coverage: XX%`
  - Note: Threshold defined in CI config (`.coveragerc` + `ci.yml`); last release (v1.5.2) measured 91.38%; must not regress from the v1.1 baseline

---

## Testing

- [x] **Unit tests: all pass**
  - Command: `pytest -v -m "not integration"`
  - Expected: `XX passed` (0 failed, 0 errors)
  - Note: All tests under `tests/` except those marked `integration`

- [x] **Integration tests: all pass**
  - Command: `pytest -v -m integration`
  - Expected: All integration tests pass (may be skipped if scenedetect/ffmpeg not available)
  - Note: Requires `scenedetect` (install `[media]` extra); ffmpeg is resolved via `ffmpeg_bin()`

- [x] **E2E smoke test passes**
  - Command: `pytest -v tests/test_e2e_smoke.py`
  - Expected: Test passes with no errors
  - Note: Validates full pipeline execution with minimal inputs

- [x] **Contract tests pass**
  - Command: `pytest -v tests/test_contract.py`
  - Expected: All contract re-export, protocol, and version tests pass
  - Note: Verifies `CONTRACT_VERSION` value and `__all__` completeness

- [x] **New v1.6.0 engineering tests pass**
  - Command: `pytest -v tests/test_cli_options.py tests/test_check_metadata_keys.py tests/test_server_ops_settings.py`
  - Expected: All pass (CLI option-alias drift, metadata-key gate, server-ops settings view)
  - Note: Adds +11 tests total vs v1.5.2

---

## Security

- [x] **SAST (bandit) passes with zero high-confidence findings**
  - Command: `bandit -r src/movie_narrator -c pyproject.toml`
  - Expected: No issues identified (or only low/medium with documented exceptions)
  - Note: v1.2.1 fixed the earlier B110 (`try_except_pass`) finding in `gpu_detect.py` by using `contextlib.suppress`

- [x] **Dependency audit (pip-audit) passes**
  - Command: `pip-audit`
  - Expected: `No known vulnerabilities found`
  - Note: Run in a clean `pip install -e ".[dev]"` environment; documented ignore-list entries must be re-evaluated

- [x] **No hardcoded secrets in code**
  - Method: Manual review + CI secret scanning (GitHub secret scanning)
  - Expected: No API keys, tokens, or credentials committed to source

- [x] **ADR-011 forbidden dependency check passes (machine-enforceable subset)**
  - Command: `python scripts/check_forbidden_deps.py`
  - Expected: No forbidden pip-installable packages found (Remotion, TypeTale, yt-dlp, Bilibili API, Playwright, IndexTTS, CosyVoice)
  - Note: Non-package red lines (e.g. copying TypeTale source, using scrapers behaviorally) still require manual code review

- [x] **FFmpeg bundle check passes**
  - Command: `python scripts/check_no_ffmpeg_bundle.py`
  - Expected: No `ffmpeg` or `ffprobe` binary found in built wheel
  - Note: Confirms the ADR-011 FFmpeg policy; also runs automatically in `.github/workflows/publish.yml` after `twine check`

---

## Documentation

- [x] **CHANGELOG.md is finalized**
  - Verification: Review `CHANGELOG.md`
  - Expected:
    - New `## [1.6.0] - <date>` heading (was `[Unreleased]`)
    - `CONTRACT_VERSION` line uses the mandated format: `- \`CONTRACT_VERSION\` remains (1, 3, 0). All 3189 tests pass (1 skipped in CI, 0 failures). +11 new tests vs v1.5.2.`
    - Version comparison links at bottom updated (`[Unreleased]` → `.../compare/v1.6.0...HEAD`, new `[1.6.0]` link)
    - Historical entries unchanged (no codename or terminology edits to old releases)

- [x] **ROADMAP reflects v1.6.0**
  - Verification: `docs/ROADMAP.md` (+ `.zh-CN.md`)
  - Expected: v1.6.0 row in the Completed table

- [x] **Current-version alignment**
  - Method: Scan **all** public docs (`docs/**/*.md` + `README.md`) — do **not** rely on a fixed file list — and grep each for the previous version string (`v1.5.2`) used as a "current"-version claim
  - Expected: No public doc still claims the old version as current; update every stale stamp (compatibility notes in `DEPLOYMENT`/`MIGRATION`/`TUTORIAL`, `mn version` output in `QUICKSTART`, `index.md` checklist label) to **v1.6.0**. Leave legitimate references intact (historical records, comparison baselines, illustrative examples). Re-run the scan to confirm.
  - Note: Also update the local `CLAUDE.md` "Current version" line (gitignored, local-only). File-agnostic, so this item never needs a new file list

- [x] **mkdocs build succeeds**
  - Command: `mkdocs build`
  - Expected: Build completes; the known `--strict` warnings (Scenario A cross-tree `../` links, griffe docstring notes) are expected and not gated per `.claude/rules/documentation.md`

---

## Release Preparation

- [x] **Version numbers are aligned**
  - Verification:
    - `pyproject.toml` → `version = "1.6.0"`
    - `src/movie_narrator/contract.py` → `CONTRACT_VERSION = (1, 3, 0)` (unchanged — no new exports in v1.6.0, do **not** bump)
    - `docs/ROADMAP.md` → CONTRACT_VERSION line shows `(1, 3, 0)` (unchanged in v1.6.0)
    - `docs/MIGRATION.md` → current-version note updated
  - Expected: Package version 1.6.0; contract version remains (1, 3, 0)

- [x] **Tag naming follows convention**
  - Format: `v1.6.0` (lowercase `v`, semver, no prefix/suffix)
  - Command: `git tag -a v1.6.0 -m "v1.6.0 - Engineering Quality: Megafile Split, CLI Option Aliasing, Settings Ops View & Metadata Key Gates"`
  - Note: Annotated tag, not lightweight; tag push MUST be separate from branch push

- [x] **Release branch merged to main**
  - Verification: feature branch merged into `main` via PR (all CI checks pass on the merge commit)
  - Note: No direct pushes to `main`; squash or rebase merge per branch protection

- [x] **PyPI publish workflow is ready**
  - Verification: `.github/workflows/publish.yml` exists and is configured
  - Expected: Trusted Publisher configured, tag push triggers publish
  - Manual verification:
    ```bash
    pip install dist/movie_narrator-1.6.0-py3-none-any.whl
    mn version  # should show 1.6.0
    ```

- [x] **GitHub Release follows the release.md spec**
  - Title: `v1.6.0 - Engineering Quality: Megafile Split, CLI Option Aliasing, Settings Ops View & Metadata Key Gates`
  - Body: the `## [v1.6.0]` section of `CHANGELOG.md` copied verbatim (per `.claude/rules/release.md`), plus a link to the full CHANGELOG
  - Exactly one **non-draft** release per tag — delete any empty draft left by `publish.yml`

- [x] **Git tag pushed**
  - Command: `git push origin v1.6.0`
  - Expected: Tag appears on GitHub, publish workflow starts, PyPI `movie-narrator==1.6.0` published
  - Note: Push tag only after all checklist items are confirmed

---

## Post-Release

- [x] **PyPI release verified**
  - Verification:
    ```bash
    pip install movie-narrator==1.6.0
    python -c "from movie_narrator.contract import CONTRACT_VERSION; print(CONTRACT_VERSION)"
    # Expected: (1, 3, 0)
    ```
  - Expected: Package installs cleanly, import works, package version 1.6.0

- [x] **Maintenance branch (by convention, not created)**
  - Verification: n/a — `v1.2.x`–`v1.4.x` were likewise not created; backports land on `main` per project convention
  - Purpose: checklist stub retained for projects that adopt maintenance branches

---

*Use this checklist for each release candidate (RC). The final RC that passes
all items becomes the v1.6.0 release.*