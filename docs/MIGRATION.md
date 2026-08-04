[![English](https://img.shields.io/badge/English-Migration-blue)](MIGRATION.md)
[![简体中文](https://img.shields.io/badge/简体中文-迁移指南-green)](MIGRATION.zh-CN.md)

# Migration Guide

This guide helps existing users of `movie-narrator` **v0.x** migrate smoothly to **v1.0 stable**. It covers breaking changes, configuration migration, plugin updates, upgrade steps, and rollback procedures.

## Overview

movie-narrator v1.0 is the first stable release with long-term API and behavior guarantees. Most v0.9.x configurations and plugins will work with minimal changes, but some breaking changes require explicit updates.

| Original Version | Target Version | Migration Effort | Notes |
|------------------|----------------|------------------|-------|
| `< v0.7.3`       | v1.0           | Medium           | Update `mn serve` binding + authentication |
| `v0.7.3 - v0.7.9`| v1.0           | Low              | Update `format` → `video_format` renaming |
| `v0.8.0 - v0.8.x`| v1.0           | Low              | Verify configuration boundary separation |
| `v0.9.x`         | v1.0           | Trivial          | Mostly backward compatible |

### What's New in v0.9.x (Take Advantage When Migrating)

v0.9.x lay the groundwork for v1.0 stability and added many reliability and operational improvements:

- **v0.9.1**: Circuit breaker + retry policies for external API calls
- **v0.9.2**: Task checkpointing + graceful shutdown
- **v0.9.3**: Batch job submission + cron scheduling
- **v0.9.4**: Dead-letter queue (DLQ) + conditional distributed rendering
- **v0.9.5**: Input sanitization + security scanning + coverage gating
- **v0.9.6**: i18n infrastructure + language-aware localized TTS voice mapping

## Breaking Changes

### Upgrading from v0.8.0 — `format` → `video_format` Renaming

**What changed:**

- In `job.yaml`, the top-level output format key was renamed from `format` to `video_format`.
- On the CLI, the flag was renamed from `--format` to `--video-format`.

**Backward compatibility:**

- The old `format` key in `job.yaml` is still accepted as a deprecated alias. A warning is printed if it's used.
- The old `--format` CLI flag is still accepted as an alias.

**Action required:**

Update your job files and scripts to use the new naming:

```yaml
# Before (v0.x):
format: "9:16"

# After (v1.0):
video_format: "9:16"
```

```bash
# Before (v0.x):
mn create -m "Inception" --format 9:16

# After (v1.0):
mn create -m "Inception" --video-format 9:16
```

### Upgrading from v0.7.3 — `mn serve` Default Binding Changed

**What changed:**

- Previously, `mn serve` defaulted to binding `0.0.0.0` (all interfaces).
- Now, `mn serve` defaults to binding `127.0.0.1` (localhost only).
- To expose the server to external network connections, you must explicitly use `--public`.

When you use `--public` to bind `0.0.0.0`, **MN_API_KEY authentication is required** (enforced since v0.8.0). The server refuses to start without an API key configured. You can bypass this check with `--insecure` for development only.

**Backward compatibility:**

- Local development workflows (`mn serve` without flags) continue to work without changes — the server is only accessible from localhost.

**Action required:**

If you were relying on the old `0.0.0.0` default to expose the server:

1. Add the `--public` flag when starting the server:
   ```bash
   mn serve --public --port 8765
   ```

2. Set `MN_API_KEY` in your `.env` or pass it via `--api-key`:
   ```bash
   # In .env
   MN_API_KEY=your-secret-api-key-here
   ```

3. Clients connecting to the public server must include the `X-API-Key` header:
   ```bash
   curl -H "X-API-Key: $MN_API_KEY" http://your-server:8765/health
   ```

## Configuration Migration

### Configuration Boundary Separation

v1.0 enforces a strict separation between two configuration layers:

| Layer              | Location    | Prefix/Format          | Purpose |
|--------------------|-------------|------------------------|---------|
| Infrastructure     | `.env`      | `MN_*` environment vars | LLM/TTS/VLM credentials, endpoints, model names, call parameters, reliability settings |
| Pipeline Behavior  | `job.yaml`  | YAML keys              | Scene detection, matching thresholds, rendering options, translation, BGM selection, presets, etc. |

**What changed:**

- Previously, some pipeline settings could be placed in `.env` with `MN_` prefix. This is no longer supported.
- All pipeline behavior **must** be configured in `job.yaml`. Only infrastructure credentials belong in `.env`.

**Action required:**

Move any pipeline parameters from `.env` into your `job.yaml` file. Example:

```env
# ❌ Before (wrong — this belongs in job.yaml):
MN_VIDEO_FPS=30
MN_TRANSLATE_ENABLED=true
```

```yaml
# ✅ After (correct — in job.yaml, rendering params live under the params: section):
params:
  render_fps: 30
  translate_source_lang: zh
  translate_provider: llm
```

### Configuration Precedence Rules (v1.0)

The engine evaluates configuration in this order (highest priority first):

1. **Explicit environment variables** (`MN_*` variables exported in the shell environment)
2. `./.env` (`.env` file in the current working directory)
3. `~/.movie-narrator/.env` (user-level config, created automatically on first run)
4. **Built-in defaults** (local Ollama endpoint, sensible operational defaults)

**Notes:**

- On first run, if neither `./.env` nor `~/.movie-narrator/.env` exists, the engine automatically creates `~/.movie-narrator/.env` from the `.env.example` template.
- You can override the user-level defaults by creating a project-specific `./.env`.

### Validate Your Configuration After Migration

After updating your files, verify that the config loads correctly. There is no dedicated `mn config check` command; instead, run a lightweight command that exercises your configuration and reports deprecation warnings or missing credentials:

```bash
mn version                    # confirm the installed version
mn plugin list                # confirm the engine loads its plugins without errors
mn resolve -m "Inception"     # exercise the resolve path against your movie library
```

Any deprecated keys (e.g., the old `format` alias) or boundary violations are reported as warnings during these commands.

## Plugin Migration

movie-narrator v1.0 uses a standardized plugin discovery and registration system. If you have custom plugins, follow these steps.

### Plugin Registration via Entry Points

All plugins must be registered via the `movie_narrator.plugins` entry point group in your plugin's `pyproject.toml`:

```toml
# pyproject.toml
[project.entry-points."movie_narrator.plugins"]
my_plugin = "my_plugin_module:MyPlugin"
```

The core engine automatically discovers and loads plugins installed in the environment via this entry point mechanism.

### Registration Decorators

Plugins use decorators to register custom components with the engine:

| Decorator          | Purpose                      |
|--------------------|------------------------------|
| `@register_step`   | Register a pipeline step     |
| `@register_tts`    | Register a TTS provider      |
| `@register_vision`| Register a VLM provider      |
| `@register_llm`    | Register an LLM provider     |
| `@register_research` | Register a research provider |

Example:

```python
from movie_narrator import register_tts
from movie_narrator.tts import TTSProvider

@register_tts("my_tts")
class MyCustomTTS(TTSProvider):
    # implementation here
    ...
```

### Version Checking

Plugins **must** check the `CONTRACT_VERSION` at import time to ensure compatibility:

```python
from movie_narrator.contract import CONTRACT_VERSION, check_version

# Require at least contract version 0.9.0
check_version((0, 9, 0))
```

If the engine's contract version is older than the plugin's requirement, an `ImportError` is raised with a clear upgrade message.

### Type Checking Rules

If your factory function returns an instance that does not conform to the expected ABC/protocol, the engine raises a `TypeError` immediately. This catches plugin integration bugs early.

Example: If you return `None` from a TTS provider factory instead of an instance satisfying `TTSProvider`, you get:

```
TypeError: Expected TTSProvider instance, got NoneType
```

**Action required for plugin authors:**

1. Ensure your plugin declares an entry point in `pyproject.toml` under `movie_narrator.plugins`.
2. Add `check_version()` call with the minimum required contract version.
3. Verify that all registration uses the official decorators from `movie_narrator.contract`.
4. Test that your factory functions return valid instances that match the expected protocols.

## CLI Migration

This table summarizes CLI changes that require updates to scripts or automation:

| Old Command / Flag           | New Equivalent               | Changed In |
|-------------------------------|-------------------------------|------------|
| `mn ... --format 9:16`       | `mn ... --video-format 9:16`  | v0.8.0     |
| `mn serve` (binds 0.0.0.0)    | `mn serve --public`           | v0.7.3     |
| (no auth on public interface) | `MN_API_KEY` / `--api-key` required | v0.8.0 |

All other CLI subcommands (`resolve`, `research`, `submit`, `artifacts`, etc.) remain backward compatible.

## CONTRACT_VERSION Semantics

v1.0 formalizes the `CONTRACT_VERSION` semantic versioning rules for the public API contract:

| Component | Meaning                                                                 |
|-----------|-------------------------------------------------------------------------|
| **MAJOR** | Breaking change: symbols removed, signatures changed. Plugins/apps depending on the old major version will break. |
| **MINOR** | New symbols/exports added (fully backward compatible). Old code continues to work. |
| **PATCH** | Bug fixes, documentation changes (no API surface change).              |

The version is bumped **only** when the public API surface changes. Internal refactoring that doesn't affect exported symbols doesn't require a version bump.

**Current version (v0.9.7):** `(0, 9, 5)`
**Target version (v1.0):** `(1, 0, 0)` — after v1.0, the contract will be frozen and backward compatibility will be guaranteed within the same major version.

### Checking Contract Version in Your Code

If you're building an application or tool that depends on `movie-narrator` as a library, check the contract version at import time:

```python
from movie_narrator.contract import CONTRACT_VERSION, check_version

# Require at least 1.0.0
check_version((1, 0, 0))
```

This ensures that if the installed version is too old, your users get a clear error message telling them to upgrade.

## Step-by-Step Upgrade Procedure

Follow these steps to upgrade from v0.x to v1.0:

### Step 1: Review Python Version Requirements

movie-narrator v1.0 requires **Python >= 3.10**. Python 3.13 is supported (via `audioop-lts` for legacy audio processing).

Check your Python version:

```bash
python --version
# Should be >= 3.10.0
```

Upgrade Python if necessary before proceeding.

### Step 2: Backup Your Current Configuration

Before upgrading, back up your existing configuration:

```bash
# Backup project .env if you have one
cp .env .env.v0-backup

# Backup any custom job files
cp my-job.yaml my-job.v0-backup.yaml

# Backup user-level env
cp ~/.movie-narrator/.env ~/.movie-narrator/.env.v0-backup
```

### Step 3: Upgrade the Package

```bash
pip install --upgrade movie-narrator
```

If you installed from git:

```bash
git pull origin main
pip install --upgrade .
```

### Step 4: Update Configuration Files

1. In all your `job.yaml` files: replace `format` → `video_format`.
2. Verify configuration boundary separation: no pipeline parameters in `.env`, all `MN_*` are infrastructure settings.
3. If you expose `mn serve` publicly: set `MN_API_KEY` in `.env` and add `--public` to your serve command.
4. Review and enable new reliability features (optional but recommended):
   - Circuit breaker: `MN_CIRCUIT_FAILURE_THRESHOLD`, `MN_CIRCUIT_RECOVERY_TIMEOUT`
   - Graceful shutdown: `MN_GRACEFUL_SHUTDOWN_TIMEOUT`
   - Artifact retention: `MN_ARTIFACT_TTL`, `MN_ARTIFACT_MAX_BYTES`

### Step 5: Run a Configuration Check

```bash
mn version
mn plugin list
```

Verify the version is correct and the engine loads plugins without errors. Address any deprecation warnings reported by the CLI.

### Step 6: Test with a Sample Job

Run a small test job to verify everything works:

```bash
mn resolve -m "Inception" --library-dir /path/to/library
```

If that succeeds, try a full render:

```bash
mn create -m "Inception" --video /path/to/inception.mp4 -o output/
```

### Step 7: Update Plugins (If Applicable)

If you use third-party plugins, check if the plugin has been updated for v1.0. Upgrade the plugin if necessary.

If you maintain your own custom plugins:

1. Add entry point registration to `pyproject.toml`.
2. Add the `check_version()` call.
3. Test plugin loading: `mn plugin list` should show your plugin.

## Rollback Guide

If you encounter issues after upgrading, roll back to your previous version:

### Step 1: Restore Configuration Files

```bash
# Restore project .env
cp .env.v0-backup .env

# Restore user-level env
cp ~/.movie-narrator/.env.v0-backup ~/.movie-narrator/.env

# Restore your job files
cp my-job.v0-backup.yaml my-job.yaml
```

### Step 2: Downgrade the Package

If you need to go back to your previous version:

```bash
# Replace X.Y.Z with your previous version
pip install movie-narrator==X.Y.Z
```

If you installed from git:

```bash
git checkout <previous-commit>
pip install .
```

### Step 3: Verify Rollback

Run a test job to confirm everything is working again:

```bash
mn version
mn resolve -m "Inception" --library-dir /path/to/library
```

## Frequently Asked Questions

### Q: Will my existing v0.9.x job files work in v1.0 without changes?

**A:** Most will. The only required change is renaming `format` to `video_format` if you were using the old key. The old key is still accepted with a warning, so technically it will still work. We recommend updating to remove the warning.

### Q: I'm still on v0.6.x — can I upgrade directly to v1.0?

**A:** Yes. Follow this guide, paying attention to the binding and authentication changes from v0.7.3 and the format renaming from v0.8.0. All the changes are cumulative but documented here.

### Q: Do I need to change anything if I only use the CLI for local rendering, not `mn serve`?

**A:** Only change `format` → `video_format` in any job files you have. The `mn serve` default change doesn't affect local-only usage. Everything else remains backward compatible.

### Q: My custom plugin worked in v0.8, what do I need to change for v1.0?

**A:** Add entry point discovery in `pyproject.toml`, add the `check_version` import check, and ensure you're using the official registration decorators from `movie_narrator.contract`. If you were already following the v0.9 plugin pattern, minimal changes are needed.

### Q: What if I don't want to add API key authentication — can I still serve publicly without it?

**A:** You can bypass the requirement with `--insecure`:

```bash
mn serve --public --insecure
```

**We strongly recommend against this for production or public internet exposure.** The API key is a simple but effective protection against unauthorized access.

### Q: Where did my `~/.movie-narrator/.env` go? Did the upgrade delete it?

**A:** The upgrade doesn't delete anything. v1.0 still reads `~/.movie-narrator/.env` as the lowest-priority configuration source after environment variables and `./.env`. If it existed before, it's still there.

### Q: What new features should I enable after migration?

**A:** We recommend enabling:

- Circuit breaker (`MN_CIRCUIT_FAILURE_THRESHOLD=5`) for stability when calling external APIs
- Artifact retention (`MN_ARTIFACT_TTL` or `MN_ARTIFACT_MAX_BYTES`) to control disk usage
- Per-language voice overrides (`MN_VOICE_ZH`, `MN_VOICE_EN`) if you produce videos in multiple languages (v0.9.6+)

### Q: Is v1.0 API stable? Will my plugins break in future 1.x releases?

**A:** Yes, v1.0 freezes the public contract. All 1.x releases will be backward compatible. Breaking changes will only happen in 2.0, and migration will be documented similarly.

## Python Version Support

| Python Version | Supported in v1.0 | Notes |
|----------------|-------------------|-------|
| 3.9 and older  | ❌ No             | Upgrade required |
| 3.10           | ✅ Yes            | Fully tested |
| 3.11           | ✅ Yes            | Fully tested |
| 3.12           | ✅ Yes            | Fully tested |
| 3.13           | ✅ Yes            | Supported via `audioop-lts` |

## Next Steps

After successfully migrating:

- Read the [Quickstart Guide](QUICKSTART.md) for an overview of new features
- See [Plugin Development](PLUGIN_DEVELOPMENT.md) if you're writing custom plugins
- Check [Deployment](DEPLOYMENT.md) for production deployment best practices

If you encounter issues not covered in this guide, please [open an issue](https://github.com/zcbacxc/movie-narrator/issues) with your migration details.
