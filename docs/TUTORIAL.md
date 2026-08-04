[![English](https://img.shields.io/badge/English-Tutorial-blue)](TUTORIAL.md)
[![简体中文](https://img.shields.io/badge/简体中文-教程-green)](TUTORIAL.zh-CN.md)

# Tutorial

A complete from-zero-to-advanced walkthrough of **movie-narrator**, a Python engine that turns a single movie title into a narrated recap video. This tutorial is written for **content creators** — you do not need to be a developer to follow it. If you are a plugin author, please read [QUICKSTART.md](QUICKSTART.md) instead.

> **Compatibility note.** This document describes version **1.0.0** governance. The engine is at **1.0.0** with `CONTRACT_VERSION=(1,0,0)`. Run `mn version` to check your installed build.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Tutorial 1 — Create your first video fast](#tutorial-1--create-your-first-video-fast)
- [Tutorial 2 — Configure `job.yaml`](#tutorial-2--configure-jobyaml)
- [Tutorial 3 — Presets & styles](#tutorial-3--presets--styles)
- [Tutorial 4 — Multilingual & voice](#tutorial-4--multilingual--voice)
- [Tutorial 5 — Advanced pipeline control](#tutorial-5--advanced-pipeline-control)
- [Tutorial 6 — Async jobs & remote service](#tutorial-6--async-jobs--remote-service)
- [Tutorial 7 — Batch & reliability](#tutorial-7--batch--reliability)
- [Tutorial 8 — Plugins](#tutorial-8--plugins)
- [Next steps](#next-steps)

---

## Prerequisites

Before you start, make sure you have:

1. **Python 3.10+** installed.
2. **movie-narrator** installed and the `mn` CLI available on your `PATH`.
3. **LLM + TTS credentials** configured in a `.env` file (see below). These are the infrastructure that powers script generation and narration.

Verify the CLI is installed:

```bash
mn --help
```

Check the version and the contract version:

```bash
mn version
```

### Environment variables (`.env`)

The `.env` file holds the **LLM + TTS infrastructure** configuration. It is separate from `job.yaml`, which controls pipeline behavior. At minimum you need an LLM provider configured.

```bash
# Copy the example and fill in your keys
cp .env.example .env
```

See the provider docs in `docs/llm-providers/` (e.g. `alibaba-bailian.md`, `zhipu.md`, `ollama.md`) to pick the one you use.

---

## Tutorial 1 — Create your first video fast

The fastest path to a finished recap video is a single `mn create` command. The only required argument is the movie name.

```bash
mn create -m "The Matrix"
```

That's it. `mn` will:

1. Resolve the movie (find its metadata).
2. Research the plot.
3. Generate a narration script.
4. Generate voiceover.
5. Detect scenes, match clips, render the video, and export deliverable files.

A freshly created video defaults to the `热血搞笑` (hot & funny) style, lasts **60 seconds**, and is rendered in **16:9** landscape.

### Key flags for your first run

```bash
# Change the duration (seconds)
mn create -m "Inception" -d 120

# Pick a different style
mn create -m "Spirited Away" -s "mainstream-dry"

# Choose a vertical 9:16 format (great for shorts)
mn create -m "Dune" -f 9:16

# Choose a voice
mn create -m "Titanic" -v "zh-CN-YunxiNeural"
```

### Where the output goes

By default deliverables are written to `output/` under your working directory. You can change the destination at any time:

```bash
mn create -m "Parasite" -o "./my-videos"
```

### Keep the cache for faster re-runs

Full re-runs recompute everything. If you want to iterate quickly, keep the intermediate cache:

```bash
mn create -m "Coco" --keep-cache
```

---

## Tutorial 2 — Configure `job.yaml`

`job.yaml` is the single file that controls **pipeline behavior** for a run. It is how you implement your "house style" without retyping flags every time.

### Configuration priority

```
CLI arguments  >  job.yaml  >  inline defaults
```

Whatever you pass on the command line wins over `job.yaml`; `job.yaml` wins over the built-in defaults. This makes it easy to override one option for a single run while keeping the rest of your config.

### A minimal `job.yaml`

```yaml
movie: "Interstellar"
duration: 60
style: "mainstream-dry"
video_format: "16:9"
subtitle_lang: "zh"
subtitle_mode: "burned"
```

### Pointing `mn` at your config

```bash
mn create --config ./job.yaml
```

Or per-run, overriding a single field from the CLI:

```bash
mn create --config ./job.yaml -d 90
```

> `CLI` wins: the `-d 90` overrides the `duration: 60` in the file for this run only.

### What `job.yaml` cannot do

`job.yaml` is about pipeline behavior. It does **not** hold API keys or secret infrastructure settings — those belong in `.env`. Keep the two concerns separate.

---

## Tutorial 3 — Presets & styles

Presets bundle a full set of style, voice, pacing, and formatting choices so you can apply a consistent look in one word.

### Built-in presets

| Preset | Description |
| --- | --- |
| `douyin-fast` | Fast-paced, short-style, optimized for short-video platforms |
| `mainstream-dry` | Dry, mainstream, restrained narration |
| `bilibili-long` | Long-form, story-driven, suited for in-depth recaps |

### List presets

```bash
mn preset
```

### Inspect a single preset

```bash
mn preset douyin-fast
```

### Apply a preset

```bash
# Apply a narration preset
mn create -m "Knives Out" -p douyin-fast
```

### Style vs. narration preset

- `-s/--style` controls the overall narration style (e.g. `热血搞笑`, `历史悬疑`, `温情催泪`).
- `-p/--narration-preset` applies a bundled narration preset (`douyin-fast`, `mainstream-dry`, `bilibili-long`).

They target different dimensions of the pipeline and can be combined.

### Fine-grained narration controls

Beyond presets, you can tune the narration directly:

```bash
mn create -m "The Godfather" \
  --narrator-perspective "first-person" \
  --focus-character "Michael Corleone"
```

---

## Tutorial 4 — Multilingual & voice

`movie-narrator` supports **Chinese** and **English** narration out of the box.

### Language basics

The default language is `zh` (Chinese). To switch to English, set the `lang` parameter in your `job.yaml`:

```yaml
# job.yaml
params:
  lang: en
```

```bash
mn create -m "The Dark Knight" --config ./job.yaml
```

### Subtitle mode

Subtitles can be original, translated, or bilingual:

```bash
# Show the original subtitle in the video
mn create -m "Lalaland" --subtitle-lang en --subtitle-mode original

# Show the translated subtitle in the video
mn create -m "Lalaland" --subtitle-lang en --subtitle-mode translated

# Show both original and translated side by side
mn create -m "Lalaland" --subtitle-lang en --subtitle-mode bilingual
```

`--subtitle-lang` enables translation (empty = off). `--subtitle-mode` accepts one of `original`, `translated`, or `bilingual`.

### Voice selection

Pick a voice directly:

```bash
mn create -m "Frozen" -v "zh-CN-YunxiNeural"
```

### Voice mapping via environment variables

For consistent multi-language voice behavior, set the mapping in your `.env`:

```bash
# Default voice if nothing else is set
MN_DEFAULT_VOICE="zh-CN-YunxiNeural"

# Override Chinese TTS voice
MN_VOICE_ZH="zh-CN-YunxiNeural"

# Override English TTS voice
MN_VOICE_EN="en-US-AriaNeural"
```

The `MN_VOICE_ZH` / `MN_VOICE_EN` variables override the per-language voice. `MN_DEFAULT_VOICE` is the fallback when no language-specific voice is configured.

---

## Tutorial 5 — Advanced pipeline control

Under the hood, a run executes a **16-step pipeline**. Most steps are *soft* — if they are not available (e.g. missing optional dependency), they are skipped gracefully instead of failing the run.

### The 16 pipeline steps

```
resolve_video        -> prepared
prepare_assets
research_plot        (soft)
generate_script
export_script_md
generate_voice
align_audio          (soft)
detect_scenes        (soft)
match_clips          (soft)
mix_bgm              (soft)
translate_subtitles  (soft)
generate_subtitle
run_qa_gate          (soft)
render_video
validate_deliverable
export_clips         (soft)
```

### Make soft steps strict

By default soft steps fail gracefully. If you need a hard guarantee that every step completes, run in strict mode:

```bash
mn create -m "Blade Runner" --strict
```

This turns the soft steps into hard failures — if any of them cannot run, the whole pipeline stops with an error.

### Pause and resume

You can pause the pipeline at a specific step and resume later from a checkpoint. This is useful for long or unreliable runs.

```bash
# Pause after generating the script
mn create -m "Her" --pause-at generate_script
```

Resume from a saved state:

```bash
mn resume --state ./pipeline_state.json
```

### Inspect intermediate results

Debug the pipeline step by step with the dedicated subcommands:

```bash
# Resolve a movie without running everything
mn resolve -m "Joker"

# Run research
mn research -m "Joker"

# Detect scenes (with a threshold, default 27.0)
mn scenes --video /path/to/joker.mp4 --threshold 27.0

# Align audio with a script
mn align --audio /path/to/voice.mp3 --script script.md

# Export clips from a scenes.json
mn clips --video /path/to/joker.mp4 --scenes ./output/scenes.json
```

These are great for troubleshooting a specific stage before you commit to a full render.

---

## Tutorial 6 — Async jobs & remote service

For long renders or when you want to run the engine as a service, use the async job model.

### Submit a job (async)

`mn submit` returns a job ID immediately instead of blocking:

```bash
mn submit -m "The Shawshank Redemption" -p douyin-fast --lang zh
```

### Manage the job queue

```bash
# Check the status of one job
mn status <job-id>

# List all tasks (optionally filtered by status)
mn tasks
mn tasks --status running
mn tasks -n 20

# Cancel a job
mn cancel <job-id>

# Wait for a job to finish
mn wait <job-id>
```

### Controlling retries

```bash
# Submit and cap retries at 5
mn submit -m "Whiplash" -p douyin-fast --max-retries 5

# Wait for completion inline
mn submit -m "Whiplash" -p douyin-fast --wait
```

### Run a remote service

Start the server:

```bash
# Defaults: 127.0.0.1:8765
mn serve

# Bind to a specific port
mn serve --port 9000

# Expose publicly (use with care)
mn serve --public

# Require an API key
mn serve --api-key "your-secret-key"
```

### Use the service API

```bash
# Download a finished artifact
mn download <job-id>

# Print the OpenAPI spec for the service
mn api-spec
```

---

## Tutorial 7 — Batch & reliability

When you need many videos, or you need to trust the pipeline to run unattended, use the reliability features.

### Batch submission

Submit several jobs at once. The engine accepts batches of **1 to 50** jobs via `mn submit` or the HTTP API.

```bash
# Submit multiple jobs
mn submit -m "Movie A" -p douyin-fast
mn submit -m "Movie B" -p douyin-fast
mn submit -m "Movie C" -p douyin-fast
```

### Cron scheduling

The engine supports scheduling recurring jobs (v0.9.3). Configure a cron expression so videos are produced on a schedule — e.g. daily at 08:00.

```yaml
# In your job config / scheduler
schedule: "0 8 * * *"
```

### Circuit breaker + retry

Network and provider calls are protected by a **circuit breaker with retry** (v0.9.1). Transient failures are retried automatically; if a provider keeps failing, the circuit opens so the pipeline fails fast instead of hanging.

### Checkpoints + resume

Checkpoints (v0.9.2) let you resume from the last good step. Combined with `--pause-at` and `mn resume`, this keeps long runs safe.

### Dead-letter queue (DLQ)

Failed jobs are routed to a dead-letter queue (v0.9.4) so you can inspect and retry them later without losing the payload. The DLQ is managed through the HTTP API (`GET/DELETE/REPLAY /deadletters`).

### Clean up completed tasks

```bash
# Remove completed tasks from the local queue
mn cleanup
```

### Distributed rendering

When several nodes are available, rendering can be distributed across workers. Distributed rendering activates **conditionally** — only when the environment and queue support it.

### Interactive retry

Some interactive failures offer an `R/S/A` prompt — **R**etry, **S**kip, **A**bort:

```bash
mn create -m "The Prestige" --retry
```

---

## Tutorial 8 — Plugins

Plugins extend the engine. You can discover, install, and manage them from the CLI.

```bash
# List installed plugins
mn plugin list

# Discover plugins from registries
mn plugin discover

# Manage registries
mn plugin registries

# Show plugin version info
mn plugin version
```

For a deep dive into authoring your own plugin, read [QUICKSTART.md](QUICKSTART.md).

### Artifacts

Manage generated artifacts:

```bash
# List artifacts
mn artifacts list

# Clean up old artifacts
mn artifacts cleanup
```

---

## Next steps

- Read [QUICKSTART.md](QUICKSTART.md) to learn how to build plugins.
- Explore the provider docs in `docs/llm-providers/` to tune your LLM setup.
- Review [BEST_PRACTICES.md](BEST_PRACTICES.md) for production tips.
- Check [DEPLOYMENT.md](DEPLOYMENT.md) when you want to run the engine as a service at scale.
- Look at the [ROADMAP.md](ROADMAP.md) to see what is coming next.

Now go create something great. Run your first command:

```bash
mn create -m "Your Favorite Movie"
```