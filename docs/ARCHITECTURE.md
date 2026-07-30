[![English](https://img.shields.io/badge/English-Architecture-blue)](ARCHITECTURE.md)
[![简体中文](https://img.shields.io/badge/简体中文-架构-green)](ARCHITECTURE.zh-CN.md)

# Architecture

## Component Overview

```text
┌─────────────────────────────────────────────────────────────┐
│                      Entry Points                           │
│   CLI (mn create/serve/submit)     Web UI (mn-web, external)│
└──────────┬──────────────────────────────┬───────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐        ┌─────────────────────┐
│  workflow.py        │        │  contract.py        │
│  (job.yaml merge)   │        │  (API boundary)     │
└─────────┬───────────┘        └─────────┬───────────┘
          │                              │
          ▼                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 pipeline/runner.py                          │
│    build_context() → run_pipeline() → 16 STEPS             │
│                                                             │
│  ├── tts/          Edge / OpenAI / MiMo providers          │
│  ├── vision/       Stub / VLM captioners                   │
│  ├── providers/    registry: LLM / TTS / Vision / Research │
│  ├── plugin_loader  @register_step / entry_points discovery│
│  └── cloud/        queue / API server / daemon / remote    │
└──────────┬──────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│                       Output                                │
│  final.mp4 · narration.mp3 · subtitle.srt · script.md     │
│  metadata.json · matches.json · clips/                     │
└─────────────────────────────────────────────────────────────┘
```

- **CLI** (`cli.py`) — entry point; parses flags, calls `workflow` or `run_pipeline` directly
- **workflow** (`workflow.py`) — optional job.yaml merge layer (CLI > YAML > Settings)
- **pipeline** (`pipeline/runner.py`) — 16-step sequential orchestrator; owns `STEPS`, `build_context`, `run_pipeline`
- **tts / vision / providers** — pluggable subsystems with registry-based dispatch (`@register_tts`, `@register_vision`, etc.)
- **cloud** (`cloud/`) — async task queue, REST API server, remote inference proxy (v0.6.x)
- **contract** (`contract.py`) — single import surface for external consumers; pins `CONTRACT_VERSION`
- **plugin_loader** (`plugin_loader.py`) — entry_points discovery + `@register_step` for custom steps

## Pipeline Overview

16-step sequential pipeline orchestrated by `pipeline/runner.py`. Before any step executes, `preflight.py` probes LLM connectivity and TTS provider configuration — failing fast with `PreflightError` instead of silently degrading to mock content.

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → translate_subtitles → generate_subtitle →
run_qa_gate → render_video → validate_deliverable → export_clips
```

### Step Categories

| Category | Steps | Status |
|----------|-------|--------|
| **Hard** (always run) | resolve_video, prepare_assets, generate_script, export_script_md, generate_voice, render_video, validate_deliverable | Must succeed |
| **Soft** (skip on missing deps) | research_plot, align_audio, detect_scenes, match_clips, mix_bgm, translate_subtitles, run_qa_gate, export_clips | Skip gracefully / soft-degrade; `--strict` to abort |

### Step Responsibilities

**Context** (`models.Context`) is the shared mutable state passed through all steps.

| Step | Cat. | Responsibility | Key Output |
|------|------|---------------|------------|
| resolve_video | hard | Locate source video from `--video`, `--library-dir`, or config | `ctx.video_path` |
| prepare_assets | hard | Validate BGM, font, intro assets exist on disk | — |
| research_plot | soft | LLM fetches movie metadata (title, cast, keywords) | `research.json` |
| generate_script | hard | LLM returns JSON → `List[ScriptSegment]` | script data |
| export_script_md | hard | Render segments to human-readable Markdown | `script.md` |
| generate_voice | hard | TTS async synthesis + sha256 content-addressable cache (7-dim key, two-level fan-out); CI uses silent fallback | `narration.mp3` + `TimedSegment[]` |
| align_audio | soft | WhisperX word-level alignment; fallback to segment-level via faster-whisper | word timestamps |
| detect_scenes | soft | PySceneDetect splits source video into `Scene` list | scene list |
| match_clips | soft | Map scenes to script segments: embedding re-rank (when `[ml]` installed) or proportional heuristic; falls back on probe/model failure | `matches.json` |
| mix_bgm | soft | Mix background music under narration; EP6 duck curve scales depth with narration energy | `mixed.mp3` |
| translate_subtitles | soft | Per-chunk translation via configured provider (default `llm`); retry-then-soft-degrade policy; CI passthrough | `ctx.translated_texts` |
| generate_subtitle | hard | Format SRT from timed segments; bilingual support (`subtitle.<lang>.srt`, `subtitle.bilingual.srt`) | `subtitle.srt` + variants |
| run_qa_gate | soft | Quality validation gate | QA report |
| render_video | hard | MoviePy composite: background + text/footage overlays + audio; CRF 18 / preset `slow` / `+faststart` | `final.mp4` + `metadata.json` |
| validate_deliverable | hard | ffprobe validation: streams, audio level, duration ratio, file size; CI skips by default | `ctx.metadata["qa_report"]` |
| export_clips | soft | Extract per-segment clips | `clips/` directory |

### Pipeline Status Model

Each soft step writes to `PipelineStatus` — one of `disabled | skipped | success | failed`:

```python
class PipelineStatus(BaseModel):
    research: StepStatus   # research_plot
    align: StepStatus      # align_audio
    scene: StepStatus      # detect_scenes
    match: StepStatus      # match_clips
    bgm: StepStatus        # mix_bgm
    export: StepStatus     # export_clips
    translate: StepStatus  # translate_subtitles (default: "skipped" — feature off)
    qa_gate: StepStatus    # run_qa_gate (default: "disabled")
```

`translate` is the only soft status whose **default** is `skipped` rather than
`disabled` — "feature off" is semantically distinct from "explicitly disabled
via `steps.translate=false` or unknown provider".

### metadata.json

Every pipeline run writes `metadata.json` — the audit and diagnostics file consumed by L2 hand-tests, CI quality gates, and downstream tooling. Key domains: `match_summary` (match-quality breakdown for jq queries), `duration_metrics` (narration timing vs target), align diagnostics (backend selection and fallback tracking), and `quality_dashboard` (cross-step score aggregation). Full field-by-field schema is documented in [METADATA_SCHEMA.md](METADATA_SCHEMA.md), organized by functional domain (match, align, script, audio, render, quality).

## Job config merge layer

Optional declarative job YAML sits **in front of** `run_pipeline`:

```text
CLI flags + optional job.yaml
        ▼
load_job_config (YAML → JobConfig)
        ▼
merge_job (CLI > YAML > Settings → ResolvedJob)
        ▼
run_pipeline(...) # STEPS order unchanged
```

- Module: `movie_narrator.workflow` (`load_job_config`, `merge_job`, `JobConfigError`)
- Soft steps honor `metadata["workflow_steps"][<field>] is False` → `status.<field> = "disabled"`
- Params whitelist (77 keys — full list in `examples/job.example.yaml` comments: scene detection, match, vision, BGM, TTS pacing, translate, research, WhisperX align, render, QA, prompt shaping, async, video sizes, platform, perspective) land in `ctx.metadata` via `build_context` copy loop
- Multi-language subtitle top-level keys: `subtitle_lang`, `subtitle_mode` (validated in `JobConfig` — `subtitle_mode ∈ {translated, bilingual}` without `subtitle_lang` raises `JobConfigError` at merge time)
- `STEPS` remains the single source of step order; since v0.5, custom steps can be added via `@register_step` plugin API (see Plugin System section below)
- YAML auto-discovery: `--config` not passed → `cwd/job.yaml` → packaged `examples/job.example.yaml` → none
- `.env.example` is the single source of truth for first-run config (read by `ensure_user_config()`, not a divergent inline template)
- Strict env/yaml boundary: `.env` (Settings) = 32 LLM + TTS infrastructure fields only; `job.yaml` (params) = 77 pipeline behavior keys; no code constants module — inline literals match example files

## Cloud Architecture (v0.6.x)

The `cloud/` package provides async job execution and remote inference capabilities, enabling the pipeline to run as a cloud service rather than only as a local CLI tool.

### Deployment modes

```text
Mode 1: Local async (single machine)
┌────────┐     ┌──────────────────┐
│  CLI   │────▶│  LocalTaskQueue  │────▶ ThreadPoolExecutor
│ (mn)   │     │  (in-process)    │      → run_pipeline()
└────────┘     └──────────────────┘

Mode 2: Remote worker (client-server)
┌────────┐     ┌──────────────────┐     ┌──────────────────┐
│  CLI   │────▶│ RemoteTaskQueue  │────▶│  TaskAPIServer   │
│ (mn)   │     │ (HTTP client)    │ HTTP│  + LocalTaskQueue│
└────────┘     └──────────────────┘     │  + WorkerDaemon   │
                                        │  → run_pipeline() │
                                        └──────────────────┘
```

### Task lifecycle

```text
pending → running → completed
              ↘         ↗
            retrying   failed
              ↘         ↗
               cancelled
```

- **`TaskStatus`**: `pending | running | retrying | completed | failed | cancelled`
- **Terminal states**: `completed`, `failed`, `cancelled`
- **Retry**: transient errors (ConnectionError, TimeoutError, RateLimitError) trigger exponential backoff up to `max_retries` (default 3)

### Key modules

| Module | Responsibility |
|--------|---------------|
| `cloud/models.py` | `Task`, `TaskRequest`, `TaskProgress`, `TaskResult`, `TaskStatus`, `TaskPriority` |
| `cloud/queue.py` | `TaskQueue` protocol + `LocalTaskQueue` (ThreadPoolExecutor) |
| `cloud/remote_queue.py` | `RemoteTaskQueue` — HTTP client implementing the same `TaskQueue` protocol |
| `cloud/api.py` | `TaskAPIServer` — REST API on stdlib `http.server` (no extra deps) |
| `cloud/daemon.py` | `run_daemon` / `WorkerDaemon` — queue + API server with signal handling |
| `cloud/worker.py` | `run_task` — pipeline wrapper with cancel + progress + retry; `CancelController` implements `RunController` |
| `cloud/storage.py` | `TaskStorage` — JSON persistence with atomic writes |
| `cloud/remote_provider.py` | `register_remote_llm` / `register_remote_tts` — proxy inference; `download_artifact` / `list_artifacts` — fetch outputs |

### REST API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/tasks` | Submit a new task |
| GET | `/tasks` | List tasks (optional `?status=` filter) |
| GET | `/tasks/{id}` | Get task details |
| DELETE | `/tasks/{id}` | Cancel a task |
| GET | `/tasks/{id}/result` | Get task result (terminal only) |
| GET | `/tasks/{id}/artifacts` | List output files |
| GET | `/tasks/{id}/download/{file}` | Download an output file |
| GET | `/health` | Health check |
| GET | `/info` | Server info (version, worker count) |

### Key design rules

- **Same protocol, different transports**: `LocalTaskQueue` and `RemoteTaskQueue` both implement `TaskQueue` — swap with zero code changes
- **No extra dependencies**: REST API uses stdlib `http.server`; remote client uses stdlib `urllib.request`
- **Cooperative cancellation**: `CancelController` implements `RunController` — the pipeline checks `is_cancelled()` at step boundaries, not mid-step
- **Progress via console wrapping**: `ProgressConsole` wraps the real `Console`, intercepting `step()` / `step_ok()` calls to update `TaskProgress` in real-time
- **Retry preserves cache**: on retry, `CancelController.reset()` is called and the pipeline re-executes from scratch — cached results (TTS segments, scene detection) are reused via content-addressable cache
- **Artifact management**: completed task outputs served via `/tasks/{id}/download/{file}` with path-traversal protection
- **Remote inference proxy**: `register_remote_llm("remote")` / `register_remote_tts("remote")` allow offloading LLM/TTS calls to a remote worker without changing pipeline code

## TTS Abstraction Layer

The `tts/` package decouples TTS backend selection from pipeline orchestration:

```text
pipeline/tts.generate_voice(ctx)
    ▼
tts.factory.get_tts_provider(settings) → TTSProvider
    ▼
provider.synthesize(text, voice, output_path) → writes mp3
    ▼
pipeline probes duration via AudioSegment.from_mp3
```

### Key design rules

- **No second implementation**: pipeline owns cache, concurrency, duration probe, audio merge; providers own audio generation only
- **CI temp-file isolation**: CI synthesizes to `output/.ci_<hash>.mp3`, probes, deletes — silent-audio files never enter cache
- **`is_ci()` single source of truth**: defined in `tts/base.py`, imported by pipeline (no duplicate `os.getenv("CI")`)
- **`PROVIDER_CACHE_VERSIONS` dict**: extensible per-provider cache version (Open/Closed Principle)
- **Credential fallback**: `openai_tts_api_key` → `llm_api_key`; `openai_tts_base_url` → `llm_base_url`; `mimo_api_key` → `llm_api_key`

### Modules

| Module | Responsibility |
|--------|---------------|
| `tts/protocol.py` | `TTSProvider` ABC — `synthesize(text, voice, output_path) -> None` |
| `tts/base.py` | `BaseTTSProvider` (CI silent fallback), `is_ci()`, `_estimate_duration_s()` |
| `tts/edge.py` | `EdgeTTSProvider` — wraps `edge_tts.Communicate` |
| `tts/openai_provider.py` | `OpenAITTSProvider` — wraps sync OpenAI SDK via `asyncio.to_thread`; voice whitelist |
| `tts/mimo_provider.py` | `MimoTTSProvider` — Xiaomi MiMo TTS via `chat.completions`; 3 models (named voice, voice clone, voice design); wav→mp3 conversion |
| `tts/factory.py` | `get_tts_provider(settings)` — settings → provider instance (no singleton) |
| `tts/cache.py` | `TTSCacheKey` dataclass, `cache_path_for()` (two-level fan-out), `PROVIDER_CACHE_VERSIONS` |
| `utils/errors.py` | `ConfigError` — cross-cutting config-error class |

## Vision Abstraction Layer (v0.4.26+)

The `vision/` package provides an abstraction layer for visual scene captioning, enabling future VLM (Vision Language Model) integration without touching match logic:

```text
pipeline/match._build_scene_captions()
    ▼
vision.factory.get_vision_captioner(name) → VisionCaptioner
    ▼
captioner.caption_scenes(scenes, video_path) → list[SceneCaption]
```

| Module | Responsibility |
|--------|---------------|
| `vision/protocol.py` | `VisionCaptioner` ABC — defines `caption_scenes()` contract + `SceneCaption` dataclass |
| `vision/stub.py` | `StubVisionCaptioner` — returns placeholder labels (flagged `is_stub=True`) |
| `vision/vlm.py` | `VLMVisionCaptioner` — real VLM (Vision Language Model) provider for scene captioning |
| `vision/factory.py` | `get_vision_captioner()` — dispatches by `vision_captioner` param (`"none"` / `"stub"` / future providers) |
| `vision/__init__.py` | Public API exports |

**Integration with match**: vision captions supplement (not replace) audio-transcript captions. When `vision_captioner="stub"`, labels are flagged as fake so the existing fake-caption guard treats them identically to placeholder labels — embedding is skipped, heuristic path runs. A real VLM provider can be registered in `factory.py` without modifying `match.py`.

## Plugin System (v0.5+)

The Plugin API provides a stable extension mechanism for adding custom pipeline steps and providers without forking the core engine:

```text
Third-party package (pyproject.toml entry_points)
    ▼
discover_plugins() — importlib.metadata entry_points("movie_narrator.plugins")
    ▼
Plugin.register(ctx: PluginContext) — calls @register_step / @register_tts / @register_vision / @register_llm / @register_research
    ▼
StepRegistry / ProviderRegistry — central registries
    ▼
runner.py — inserts registered steps into STEPS at before/after positions
```

### Key modules

| Module | Responsibility |
|--------|---------------|
| `plugin_loader.py` | `StepRegistry`, `Plugin` protocol, `PluginContext`, `load_plugin()`, `discover_plugins()`, `list_available_plugins()` |
| `providers/registry.py` | `ProviderRegistry`, `register_tts`, `register_vision`, `register_llm`, `register_research`, `tts_registry`, `vision_registry`, `llm_registry`, `research_registry` |
| `presets/` | Narration preset system (`list_presets()`, `get_preset()`) |

### Plugin protocol

A plugin is any object with a `name` property and a `register(ctx: PluginContext)` method:

```python
from movie_narrator import PluginContext, register_step

class MyPlugin:
    name = "my-plugin"

    def register(self, ctx: PluginContext) -> None:
        ctx.steps.register("my_step", my_func, soft=True, after="render_video")
        ctx.services.logger.info("My plugin registered")
```

Plugins are discovered via `importlib.metadata` entry points under the `movie_narrator.plugins` group. See `examples/plugins/watermark/` for a complete reference implementation.

## Pipeline Pause/Resume (EP9, v0.4.26+)

The pipeline supports human-in-the-loop pause points via `PipelinePaused` exception and state serialization:

```text
mn create ... --pause-at script
    ▼
runner: after "generate_script" step completes
    ▼
_save_pipeline_state(ctx) → output_dir/pipeline_state.json
    ▼
raise PipelinePaused(completed_step="generate_script")

mn resume <output_dir>
    ▼
_load_pipeline_state(path) → Context (SilentConsole auto-injected)
    ▼
run_pipeline(ctx, start_step="align_audio")  # skips completed steps
```

**State file** (`pipeline_state.json`): serializes all `Context` fields except `services` (non-serializable). On resume, `SilentConsole` is auto-injected via `model_validator`, then replaced with a real `Console` by the `mn resume` command.

**Pause points**: `--pause-at script` (after script generation) or `--pause-at match` (after scene matching). User can edit `script.md` or `matches.json` before resuming.

## Scene Filtering (WP6, v0.5+)

The `pipeline/scene_filter.py` module provides three scene-filtering features that improve narration quality by removing non-content segments and biasing selection toward highlights:

| Feature | Param | Description |
|---------|-------|-------------|
| **Intro skip** | `scene_skip_intro` | Auto-detects and skips intro/logo sequences at the start of videos using luminance and motion analysis |
| **Dark frame detection** | `scene_dark_threshold` | Filters near-black frames (below luminance threshold) that would waste narration budget on non-content segments |
| **Highlight window** | `scene_highlight_window` | Configurable time-window-based scene prioritization — bias scene selection toward user-specified highlight ranges |

These params are added to `PARAM_WHITELIST` via the UnifiedParamSchema and flow through the standard `build_context` → `ctx.metadata` path.

## Web UI Layer

> **The Web UI is a separate project.** Since the monorepo split, the FastAPI + React SPA stack lives in an external repository: [`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web). Install and run it as an independent package:
>
> ```bash
> pip install movie-narrator-web
> mn-web            # launches the FastAPI + React SPA (port 8760)
> ```

The external web package consumes the core engine **only** through the contract surface defined in `contract.py`. There is no `mn web` command or `[web]` extra in the core package — `fastapi`, `uvicorn`, and `python-multipart` are not dependencies of `movie-narrator`.

### Contract boundary

```text
movie-narrator-web  →  contract.py  →  pipeline/runner.py (build_context, run_pipeline, PARAM_WHITELIST)
                                →  pipeline/errors.py (PipelineCancelled, RunController, StepAction, ...)
                                →  utils/console.py (BaseConsole, Console, SilentConsole)
                                →  utils/sanitize.py (sanitize_filename)
```

`contract.py` is the **single import surface** — the web package must not import any internal module directly. `CONTRACT_VERSION = (0, 6, 1)` is checked at import time to refuse mismatched engine versions. The full symbol table is documented in [docs/sdk/contract.md](sdk/contract.md).

### Key design rules

- **No second implementation**: the web package calls `build_context` + `run_pipeline` — the same functions the CLI uses
- **Cancel is runtime-only**: `RunController` / `PipelineCancelled` never enter `Context`, `PipelineStatus`, or `metadata.json`. Cancel is a distinct terminal path (not warn, not error, does not trip `--strict`)
- **empty = no override**: form fields left blank do NOT inject into `params` — Settings (`.env` / `MN_*`) defaults apply
- **Uploads to a stable dir**: uploaded files go to `output/_uploads`, never to ad-hoc temp dirs or the `output/` movie folder

## Extension Points

- **New pipeline step (recommended)**: use `@register_step("name", ...)` decorator via the Plugin API. The step is auto-discovered if packaged as an entry_points plugin, or can be loaded manually via `load_plugin()`. See `examples/plugins/watermark/` for a reference implementation.
- **New pipeline step (legacy)**: append directly to `STEPS` in `pipeline/runner.py`. Signature must be `(ctx: Context) -> Context`.
- **Swap TTS/renderer/LLM**: replace `pipeline/tts.py`, `pipeline/render.py`, or `utils/llm.py` while keeping the step function signature.
- **New TTS/Vision/LLM/Research provider (recommended)**: use `@register_tts("name")`, `@register_vision("name")`, `@register_llm("name")`, or `@register_research("name")` decorator via the Provider Registry. The provider is auto-discovered if packaged as an entry_points plugin.
- **New VisionCaptioner provider (legacy)**: implement `VisionCaptioner` ABC in `vision/`, register in `vision/factory.py`. See `vision/stub.py` for reference. Match logic auto-detects fake vs real captions via `is_stub` flag.
- **Pipeline pause/resume**: `--pause-at script|match` pauses after the step; `mn resume <output_dir>` continues. State serialized to `pipeline_state.json`.
- **Remote inference**: `mn serve` starts a worker daemon; `mn submit --remote <url>` submits tasks to a remote worker; `register_remote_llm` / `register_remote_tts` proxy inference calls.
- **New CLI command**: add `@app.command()` in `cli.py`.
- **Frontend / WebUI**: work inside the [`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web) repository. Any new engine capability the web package needs must be exposed through `contract.py` (bump `CONTRACT_VERSION` accordingly). See `docs/CONTRIBUTING.md` → *Frontend Development*.

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Flat sequential STEPS list | No event bus or DI container; flow is explicit and inspectable |
| Soft/hard step split | Optional deps (PySceneDetect, WhisperX) don't break core pipeline |
| Content-addressable TTS cache | Avoids redundant API calls; key includes version + style_prompt |
| `PipelineStatus` model | Every soft step's outcome is introspectable in `metadata.json` |
| `--strict` flag | Turns soft failures into hard aborts for CI or production use |
| `usable_clips` filter in render | Ignores accidental `source="fallback"` rows (construction default) |
| `TaskQueue` protocol abstraction | Local and remote deployments share the same API surface |
| Stdlib-only REST API | Cloud deployment without adding FastAPI/uvicorn as core dependencies |
| `contract.py` as sole import boundary | Web package versioned independently via `CONTRACT_VERSION`, not package version |
