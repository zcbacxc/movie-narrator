[![English](https://img.shields.io/badge/English-Release_Checklist-blue)](RELEASE_CHECKLIST.md)
[![简体中文](https://img.shields.io/badge/简体中文-发布清单-green)](RELEASE_CHECKLIST.zh-CN.md)

# v1.5.1 Release Checklist

> **Definition of Done for the v1.5.1 release.** Every item on this list
> must be verified and checked off before the v1.5.1 tag is created and the
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
  - Note: Threshold defined in CI config (`.coveragerc` + `ci.yml`); v1.5.1 measured 91.41%; must not regress from the v1.1 baseline

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

- [ ] **New v1.5.1 community/governance tests pass**
  - Command: `pytest -v tests/test_v151_presets.py tests/test_v151_ratelimit.py tests/test_v151_cost_ledger.py`
  - Expected: All pass (preset install/validation, bucket math, 429 path, ledger counters)
  - Note: Adds +88 tests total vs v1.5.0

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

- [ ] **No hardcoded secrets in code**
  - Method: Manual review + CI secret scanning (GitHub secret scanning)
  - Expected: No API keys, tokens, or credentials committed to source

- [x] **ADR-011 forbidden dependency check passes (machine-enforceable subset)**
  - Command: `python scripts/check_forbidden_deps.py`
  - Expected: No forbidden pip-installable packages found (Remotion, TypeTale, yt-dlp, Bilibili API, Playwright, IndexTTS, CosyVoice)
  - Note: Non-package red lines (e.g. copying TypeTale source, using scrapers behaviorally) still require manual code review

- [ ] **FFmpeg bundle check passes**
  - Command: `python scripts/check_no_ffmpeg_bundle.py`
  - Expected: No `ffmpeg` or `ffprobe` binary found in built wheel
  - Note: Confirms the ADR-011 FFmpeg policy; also runs automatically in `.github/workflows/publish.yml` after `twine check`

---

## Documentation

- [x] **CHANGELOG.md is finalized**
  - Verification: Review `CHANGELOG.md`
  - Expected:
    - New `## [1.5.1] - <date>` heading (was `[Unreleased]`)
    - `CONTRACT_VERSION` line uses the mandated format: `- `CONTRACT_VERSION` remains (1, 3, 0). All NNN tests pass (N skipped in CI, 0 failures). +M new tests vs v1.5.0.`
    - Version comparison links at bottom updated (`[Unreleased]` → `.../compare/v1.5.1...HEAD`, new `[1.5.1]` link)
    - Historical entries unchanged (no codename or terminology edits to old releases)

- [x] **ROADMAP reflects v1.5.1**
  - Verification: `docs/ROADMAP.md` (+ `.zh-CN.md`)
  - Expected: v1.5.1 row in the Completed table; the v1.5 section marks v1.5.0 and v1.5.1 as shipped

- [x] **Current-version alignment**
  - Method: Scan **all** public docs (`docs/**/*.md` + `README.md`) — do **not** rely on a fixed file list — and grep each for the previous version string (`v1.5.0`) used as a "current"-version claim
  - Expected: No public doc still claims the old version as current; update every stale stamp (compatibility notes in `DEPLOYMENT`/`MIGRATION`/`TUTORIAL`, `mn version` output in `QUICKSTART`, `index.md` checklist label) to **v1.5.1**. Leave legitimate references intact (historical records, comparison baselines, illustrative examples). Re-run the scan to confirm.
  - Note: Also update the local `CLAUDE.md` "Current version" line (gitignored, local-only). File-agnostic, so this item never needs a new file list

- [ ] **mkdocs build succeeds**
  - Command: `mkdocs build`
  - Expected: Build completes with no warnings or errors

---

## Release Preparation

- [ ] **Version numbers are aligned**
  - Verification:
    - `pyproject.toml` → `version = "1.5.1"`
    - `src/movie_narrator/contract.py` → `CONTRACT_VERSION = (1, 3, 0)` (unchanged — no new exports in v1.5.1, do **not** bump)
    - `docs/ROADMAP.md` → CONTRACT_VERSION line shows `(1, 3, 0)` (unchanged in v1.5.1)
    - `docs/MIGRATION.md` → current-version note updated
  - Expected: Package version 1.5.1; contract version remains (1, 3, 0)

- [ ] **Tag naming follows convention**
  - Format: `v1.5.1` (lowercase `v`, semver, no prefix/suffix)
  - Command: `git tag -a v1.5.1 -m "v1.5.1 - Community & Governance: Preset Sharing, Rate Limiting & Usage Ledger"`
  - Note: Annotated tag, not lightweight; tag push MUST be separate from branch push

- [ ] **Release branch merged to main**
  - Verification: feature branch merged into `main` via PR (all CI checks pass on the merge commit)
  - Note: No direct pushes to `main`; squash or rebase merge per branch protection

- [ ] **PyPI publish workflow is ready**
  - Verification: `.github/workflows/publish.yml` exists and is configured
  - Expected: Trusted Publisher configured, tag push triggers publish
  - Manual verification:
    ```bash
    pip install dist/movie_narrator-1.5.1-py3-none-any.whl
    mn version  # should show 1.5.1
    ```

- [ ] **GitHub Release follows the release.md spec**
  - Title: `v1.5.1 - Community & Governance: Preset Sharing, Rate Limiting & Usage Ledger`
  - Body: the `## [v1.5.1]` section of `CHANGELOG.md` copied verbatim (per `.claude/rules/release.md`), plus a link to the full CHANGELOG
  - Exactly one **non-draft** release per tag — delete any empty draft left by `publish.yml`

- [ ] **Git tag pushed**
  - Command: `git push origin v1.5.1`
  - Expected: Tag appears on GitHub, publish workflow starts, PyPI `movie-narrator==1.5.1` published
  - Note: Push tag only after all checklist items are confirmed

---

## Post-Release

- [ ] **PyPI release verified**
  - Verification:
    ```bash
    pip install movie-narrator==1.5.1
    python -c "from movie_narrator.contract import CONTRACT_VERSION; print(CONTRACT_VERSION)"
    # Expected: (1, 3, 0)
    ```
  - Expected: Package installs cleanly, import works, package version 1.5.1

- [ ] **Maintenance branch exists**
  - Verification: `v1.5.x` branch present on origin (created at v1.5.1)
  - Purpose: Backport security and critical bug fixes for v1.x users

---

*Use this checklist for each release candidate (RC). The final RC that passes
all items becomes the v1.2.1 release.*