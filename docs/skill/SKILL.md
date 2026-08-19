---
name: movie-narrator
description: |
  Turn a single text prompt into a narrated movie-recap video. The `mn` CLI
  orchestrates a 16-step pipeline (resolve → assets → research → script →
  TTS → align → scenes → match → BGM → translate → subtitle → QA → render →
  validate → export). This skill lets AI agents (Claude Code, Codex, Cursor,
  Copilot, etc.) drive the CLI directly and know exactly which commands exist,
  what they do, and how to chain them.
version: 1.0.0
---

# movie-narrator CLI Skill

`mn` is a Python CLI that generates narrated movie-recap videos from a single
prompt. This document is the machine-readable capability map for AI agents —
read it before issuing `mn` commands so you use the right command, flags, and
conventions.

## Install & Verify

```bash
pip install -e ".[dev]"        # dev mode with test deps
mn version                     # print version + CONTRACT_VERSION
mn --help                      # full command list
```

External prerequisites: **FFmpeg** on `PATH` (the renderer resolves it via
`utils/ffmpeg_bin.ffmpeg_bin()`, preferring the bundled imageio-ffmpeg build, then
the system binary). Optional extras: `[media]` (scene detection), `[ml]`
(WhisperX alignment, embedding matching), `[full]` (all extras).

## Command Reference

### Core generation

| Command | Purpose |
|---------|---------|
| `mn create` | Run the full pipeline: prompt → narrated recap video |
| `mn race` | Run multiple candidate configs in parallel and pick the best |
| `mn imitate` | Analyze a reference video and imitate its style/rhythm |
| `mn resume` | Resume a paused pipeline from a checkpoint |

### Pipeline sub-steps

| Command | Purpose |
|---------|---------|
| `mn resolve` | Resolve a movie from a library directory |
| `mn research` | Run plot research (facts/validation) |
| `mn scenes` | Detect scenes in a video file |
| `mn align` | Align audio with script using WhisperX |
| `mn clips` | Export clips from scenes.json |

### Task queue / async (v0.6+)

| Command | Purpose |
|---------|---------|
| `mn submit` | Submit an async narration task |
| `mn status` | Show a single task's status |
| `mn tasks` | List tasks |
| `mn cancel` | Cancel a running task |
| `mn wait` | Wait for task completion |
| `mn cleanup` | Clean up terminal tasks |
| `mn serve` | Start the remote inference API server |
| `mn download` | Download artifacts from a remote server |

### Plugins, presets, registry, artifacts

| Command | Purpose |
|---------|---------|
| `mn plugin list` | List installed entry-point plugins |
| `mn plugin discover` | Discover and load all plugins |
| `mn plugin registries` | Show registered providers/steps |
| `mn plugin version` | Show CONTRACT_VERSION |
| `mn preset` | List presets or show preset details |
| `mn api-spec` | Dump the REST API OpenAPI 3.1 spec |
| `mn artifacts` | Artifact storage and TTL lifecycle (sub: `list` / `cleanup`) |

### Environment & diagnostics

| Command | Purpose |
|---------|---------|
| `mn doctor` | Pre-flight environment check — ffmpeg, optional extras, config (exit code 1 if anything missing) |

## Common Workflows

### Generate a video (basic)

```bash
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60
```

### Generate from a job YAML config

```bash
mn create --config examples/job.example.yaml
```

### Offline / CI smoke run (no network TTS)

```bash
CI=1 mn create --movie "CI-Test" --style "热血搞笑" --duration 10 --keep-cache
```

### Async task queue

```bash
mn submit -m "飞驰人生" -p douyin-fast    # submit async task
mn status <task_id>                       # show task status
mn serve --port 8765 --max-workers 2      # start daemon + REST API server
```

### Pause & resume (human-in-the-loop)

```bash
mn create --movie "飞驰人生" --pause-at script                  # pause after script
mn resume --state output/<movie>/pipeline_state.json           # resume from checkpoint
```

## Key `mn create` Flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--movie / -m` | — | Movie title/name (required action) |
| `--style / -s` | 热血搞笑 | Narration style |
| `--duration` | 60 | Target duration in seconds |
| `--voice / -v` | provider default | TTS voice |
| `--video-format / -f` | 16:9 | Aspect ratio `16:9` or `9:16` (legacy `--format` alias accepted) |
| `--narration-preset / -p` | — | Narration preset: `douyin-fast` / `mainstream-dry` / `bilibili-long` |
| `--bgm` | — | Background music file |
| `--no-bgm` | — | Disable BGM |
| `--no-clips` | — | Skip clip export |
| `--strict` | — | Make soft-step failures fatal |
| `--retry` | — | Interactive retry on hard-step failure |
| `--config` | — | Job YAML config path |
| `--subtitle-lang` | — | Subtitle target language |
| `--subtitle-mode` | — | Subtitle mode |
| `--narrator-perspective` | — | Narrator perspective: `omniscient` / `character` / `detective` |
| `--focus-character` | — | Focus character anchor |
| `--output-dir` | — | Output directory |
| `--pause-at` | — | Pause after a given step |
| `--log-level` / `--verbose` | — | Logging controls |

## Pipeline & Soft Steps

The pipeline is a flat sequence of steps over a shared `Context`. Eight steps
are **soft** (optional): `research_plot`, `align_audio`, `detect_scenes`,
`match_clips`, `mix_bgm`, `translate_subtitles`, `run_qa_gate`,
`export_clips`. Each writes a 5-state outcome
(`disabled`/`skipped`/`success`/`failed`/`partial`). Use `--strict` to make
soft failures fatal.

## Conventions an Agent Must Follow

- **Config priority**: CLI flags > YAML config > Settings defaults.
- **Settings boundary**: `.env` (Settings) holds LLM + TTS credentials,
  endpoints, models, and call params **only**. All pipeline behavior is
  configured via `job.yaml` params.
- **Output layout**: `output/<sanitized_movie>/` with a `cache/` dir (deleted
  unless `--keep-cache`). Generated artifacts are gitignored.
- **CI/offline**: use `CI=1` to force silent-audio fallback (no Edge-TTS
  network calls) so the pipeline runs end-to-end without network.
- **When a task has no `--movie`, the CLI may prompt interactively.** Prefer
  passing `--movie` explicitly in non-interactive/agent use.
- Generated artifacts (`*.mp4`, `*.mp3`, `*.srt`, `*.json`, `output/`) are
  gitignored — do not commit them.

## Troubleshooting Agent Interactions

- **"No plugins found via entry_points"** — expected if no out-of-tree plugin
  packages are installed; not an error.
- **Optional extras missing / pipeline soft-degrades** — run `mn doctor` for a
  per-dependency status report. Each row is one of three states:
  - `installed` — usable, no action needed.
  - `not installed` — the module is absent; the hint shows the `pip install`
    command for the corresponding extra (`[media]` / `[ml]`).
  - `missing deps` — the package is installed but a transitive dependency
    failed to import; the hint names the exact missing module (e.g.
    `torchaudio` for `whisperx`). Install that module rather than reinstalling
    the whole extra.
- **FFmpeg not found** — ensure FFmpeg is on `PATH`; the renderer resolves it
  via `utils/ffmpeg_bin.ffmpeg_bin()` (bundled imageio-ffmpeg first, then system).
- **CJK font fallback** — `assets/fonts/NotoSansSC-Regular.otf` → system
  paths → install hint if missing.
- **`mn` command missing** — run `pip install -e ".[dev]"` first.