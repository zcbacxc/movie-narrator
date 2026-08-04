[![English](https://img.shields.io/badge/English-Stability-blue)](STABILITY.md)
[![简体中文](https://img.shields.io/badge/简体中文-稳定性承诺-green)](STABILITY.zh-CN.md)

# API Stability Promise

This document defines the stability guarantees for the `movie-narrator` public API.
Starting with **v1.0.0**, the project provides formal semantic versioning and
backward-compatibility commitments for all exported symbols, configuration
interfaces, and plugin extension points.

## Stability Promise

**As of v1.0.0, the `movie_narrator.contract` API surface is declared stable.**

All symbols exported from `movie_narrator.contract` — including models,
protocols, registries, decorators, error types, and cloud SDK types — are
covered by this stability promise. External consumers (web UI, plugins,
third-party tools) may depend on these symbols with confidence that their
signatures and behavior will not change incompatibly within the same major
version.

### What is Covered

- All names listed in `movie_narrator.contract.__all__`
- Public method signatures and parameter names of exported classes and functions
- Return types and error types raised by exported functions
- `CONTRACT_VERSION` tuple format and comparison semantics
- Plugin protocol (`Plugin`, `PluginContext`, entry point discovery)
- CLI command names and their documented flags
- `job.yaml` schema (top-level keys and their documented types)
- `.env` variable names with `MN_` prefix

### What is NOT Covered

- Internal modules under `movie_narrator.pipeline`, `movie_narrator.utils`,
  `movie_narrator.tts`, etc. — these are implementation details and may change
  without notice. Always import from `movie_narrator.contract` or
  `movie_narrator` top-level re-exports.
- Private attributes and methods (names starting with `_`)
- Default values that are not explicitly documented as stable
- Output file formats (video encoding, subtitle styling) — these are
  implementation-dependent and may change between minor versions
- Performance characteristics and timing
- Experimental or preview features explicitly marked as unstable

## Versioning Policy

`movie-narrator` follows [Semantic Versioning 2.0.0](https://semver.org/).
The package version in `pyproject.toml` and the `CONTRACT_VERSION` tuple in
`contract.py` are always bumped together in the same release.

### Package Version (pyproject.toml)

| Component | Meaning |
|-----------|---------|
| **MAJOR** | Breaking changes to the public API. Users and plugins will need to update their code. |
| **MINOR** | New features added in a backward-compatible manner. Existing code continues to work without changes. |
| **PATCH** | Bug fixes, security patches, and documentation updates. No API surface changes. |

### CONTRACT_VERSION (contract.py)

`CONTRACT_VERSION` is a `(major, minor, patch)` tuple that tracks the public
API surface independently of release marketing. It follows the same semver
rules:

| Component | Meaning |
|-----------|---------|
| **MAJOR** | Breaking removals or signature changes to exported symbols. |
| **MINOR** | New exports added (backward compatible). Old code continues to work. |
| **PATCH** | Bug fixes, doc changes — no change to the API surface. |

> **Note**: `CONTRACT_VERSION` is bumped only when the public API surface
> changes. Internal refactoring, bug fixes, and performance improvements
> that do not affect exported symbols do not require a CONTRACT_VERSION bump.

### Version Compatibility Rule

A consumer requiring contract version `(X, Y, Z)` is compatible with any
installed version `(A, B, C)` where:

- `A == X` (same major version)
- `B >= Y` (minor version is at least the required minimum)
- `C >= Z` when `B == Y` (patch version is at least the required minimum
  within the same minor line)

Consumers should use `check_version()` to enforce this at import time:

```python
from movie_narrator.contract import check_version
check_version((1, 0, 0))
```

## Deprecation Policy

When a public API feature needs to be removed or changed in a breaking way,
the project follows a deprecation-first policy:

1. **Deprecation announcement**: The feature is marked as deprecated in a
   **minor** release. A deprecation warning is issued at runtime when the
   feature is used. Documentation is updated to indicate the deprecation
   and recommend the replacement approach.

2. **Deprecation window**: The deprecated feature remains available for at
   least **one full minor release cycle** (e.g., deprecated in v1.2, removed
   no earlier than v1.3). For significant or widely-used features, the
   deprecation window may be extended to two minor releases.

3. **Removal**: The feature is removed in the next **major** version. In
   exceptional cases (security vulnerabilities, severe correctness bugs),
   a feature may be removed earlier with appropriate notice.

### Deprecation Warnings

All deprecations use Python's `warnings.warn()` with `DeprecationWarning`
category and include:

- The name of the deprecated feature
- The version in which it was deprecated
- The version in which it will be removed
- The recommended replacement

Example:

```
DeprecationWarning: `old_function()` is deprecated since v1.2 and will be
removed in v2.0. Use `new_function()` instead.
```

## Upgrade Guarantees

### Within the Same Major Version (v1.x)

- **Zero breaking changes**: No symbol removals, no signature changes to
  existing functions, no removal of CLI flags or config keys.
- **New features are additive**: New exports, new CLI flags, and new config
  options are added in minor releases without affecting existing usage.
- **Bug fixes are safe**: Patch releases fix bugs without changing documented
  behavior. If a bug fix changes observable behavior in a way that might
  break user code, it is treated as a minor release with migration notes.
- **Deprecation warnings first**: Any future removal is preceded by at least
  one minor release of deprecation warnings.

### Between Major Versions (v1.x → v2.0)

- Breaking changes are allowed and expected.
- A complete [Migration Guide](MIGRATION.md) is provided for every major
  version bump.
- All breaking changes are documented in `CHANGELOG.md` under the
  `Breaking Changes` section.
- The previous major version receives **security and critical bug fix
  support for at least 6 months** after the new major version is released.

### v0.x → v1.0 Transition

The v1.0 release is the first stable release. The v0.x series was a
rapid-development pre-stable phase where breaking changes occurred between
minor versions. Users upgrading from v0.x should consult the
[Migration Guide](MIGRATION.md) for a complete list of changes and upgrade
steps.

Key facts about the v1.0 transition:

- `CONTRACT_VERSION` moves from `(0, 9, 5)` to `(1, 0, 0)`.
- This is a MAJOR version bump — 1.0 is the first stable release, not a
  continuation of the v0.x compatibility model.
- The API surface declared in `contract.py` is frozen and will remain
  backward compatible throughout the v1.x series.
- All v0.9.x features are preserved in v1.0; there are no removals in the
  v1.0 release itself.

## Python Version Support

`movie-narrator` supports the following Python versions:

| Python Version | Supported in v1.x | Support Status |
|----------------|-------------------|----------------|
| 3.9 and older  | ❌ No             | Never supported |
| 3.10           | ✅ Yes            | Primary target, fully tested |
| 3.11           | ✅ Yes            | Fully tested |
| 3.12           | ✅ Yes            | Fully tested |
| 3.13           | ✅ Yes            | Supported via `audioop-lts` |

### Support Policy

- A minimum of **3** Python minor versions are supported at all times.
- New Python minor versions are added in the next minor release after their
  stable release, provided all dependencies support them.
- Python versions are dropped only in **major** releases. The end-of-support
  date for each Python version is announced at least one minor release in
  advance.
- Security and bug fix support is provided for all supported Python versions.

## Contract Compatibility Matrix

This table shows which `CONTRACT_VERSION` was introduced in each package
release and what API surface it corresponds to.

| Package Version | CONTRACT_VERSION | Key API Additions / Changes |
|-----------------|------------------|------------------------------|
| v0.4.x          | `(0, 4, 0)`      | Initial contract layer, core pipeline exports |
| v0.5.x          | `(0, 5, 0)`      | Plugin API, registries, Step/Plugin protocols |
| v0.6.0          | `(0, 6, 0)`      | Cloud / Task Queue types, `Task`, `TaskQueue` |
| v0.6.1          | `(0, 6, 1)`      | Remote inference, `RemoteTaskQueue`, `WorkerDaemon` |
| v0.8.1          | `(0, 8, 1)`      | Observability: `JsonFormatter`, `MetricsRegistry` |
| v0.8.2          | `(0, 8, 2)`      | Health probes + OpenAPI: `build_health_payload`, `build_openapi_spec` |
| v0.8.3          | `(0, 8, 3)`      | Artifact storage: `ArtifactInfo`, `LocalArtifactStore`, `S3ArtifactStore` |
| v0.9.1          | `(0, 9, 1)`      | Reliability: `CircuitBreaker`, `RetryPolicy`, `with_retry` |
| v0.9.2          | `(0, 9, 2)`      | Task lifecycle: `TaskCheckpoint`, `CheckpointStore`, `ResumePlan` |
| v0.9.3          | `(0, 9, 3)`      | Batch & Schedule: `Batch`, `BatchRequest`, `JobScheduler`, `ScheduleRequest` |
| v0.9.4          | `(0, 9, 4)`      | DLQ + Distributed rendering: `DeadLetterRecord`, `DistributedRenderPlanner` |
| v0.9.6          | `(0, 9, 5)`      | i18n / voice mapping: `DEFAULT_VOICE_MAP`, `resolve_voice` |
| **v1.0.0**      | **`(1, 0, 0)`**  | **API freeze — first stable release. All v0.9.6 exports preserved; stability guarantees begin.** |

### Forward Compatibility

Code written against `CONTRACT_VERSION (1, 0, 0)` will work unmodified with
all future v1.x releases. New exports added in later v1.x releases will have
higher minor version numbers but will not break existing code.

### Checking Compatibility

Use `check_version()` in your plugin or application to ensure the installed
engine meets your minimum requirements:

```python
from movie_narrator.contract import check_version

# Require at least v1.0.0 (stable API)
check_version((1, 0, 0))

# Or require a specific minor version with new features
check_version((1, 2, 0))
```

---

*This stability policy is effective as of v1.0.0. For questions about
API stability or deprecation timelines, please open an issue on GitHub.*
