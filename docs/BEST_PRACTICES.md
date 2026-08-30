[![English](https://img.shields.io/badge/English-Best_Practices-blue)](BEST_PRACTICES.md)
[![简体中文](https://img.shields.io/badge/简体中文-最佳实践-green)](BEST_PRACTICES.zh-CN.md)

# Best Practices

> **Core insight**: 40% of output quality is determined by source material, 30% by LLM script quality, 20% by BGM and publishing packaging. The engine's algorithms can only optimize the remaining 10%. Check these "bypass" factors before tweaking code parameters.

---

## Source Selection

The same engine and script produce far better results with trailers, HD sources, or subtitle-track sources than with camcorded full films. Source material is the single biggest quality ceiling.

### Source Priority

| Priority | Source Type | Why It Works | Notes |
|----------|-------------|-------------|-------|
| 1 | Official trailer | Extremely high highlight density, 2 min condensed essence, natural editing rhythm for short videos | May lack key plot shots; better for 60s than 120s |
| 2 | Official featurette | Good quality, designed shot combinations | Short duration, may need splicing |
| 3 | Full film 1080p+ | Complete footage, free scene selection | Engine must find highlights itself, lower info density |
| 4 | Full film 720p | Usable but subtitle clarity limited | Blurry after 9:16 upscale |
| — | Camcord / watermarked | Poor quality, noisy audio | Not recommended |

### Quality Checklist

Run the helper tool to check source quality:

```bash
python scripts/source_check.py /path/to/your-film.mp4
```

Manual checks:

- Resolution >= 1280x720 (portrait at least 720x1280)
- Has audio track (no audio causes WhisperX failure, match falls back to heuristic)
- Duration >= 3x target output (60s output needs at least 3 min source)
- No burned-in subtitles (interferes with framing, engine cannot remove)
- No channel logo / watermark (bottom-right watermark may be enlarged after portrait crop)

### Practical Tips

- **Trailers first**: 90% of viral recaps use trailers, not full films. Trailers are pre-cut highlight reels — match success rate jumps dramatically.
- **Multi-source splice**: If full film has good quality but scattered highlights, manually edit a 3-5 min highlight reel as the source.
- **Skip intro**: Use `match_skip_intro_sec: 30` to skip studio logos and opening black screens.

---

## LLM Selection

LLM quality in the script phase (Phase 1 beats + Phase 2 expand) directly determines the narrative ceiling. Weak models produce loose beats and weak hooks that no amount of match/render tuning can fix.

### Model Recommendations

| Use Case | Recommended Models | Notes |
|----------|-------------------|-------|
| Script generation (production) | GPT-4o / Claude 3.5 Sonnet / DeepSeek V3 | Strong Chinese narration, high-quality hooks |
| Script generation (testing) | Qwen-72B / GLM-4 | Usable but hooks tend to be formulaic |
| Script generation (dev) | Any 7B+ local model | Engineering validation only, not for output |
| Translation | GPT-4o-mini / Qwen-72B | High error tolerance for translation tasks |

### Configuration

Switch LLM provider via environment variables:

```bash
# .env file
MN_LLM_PROVIDER=openai
MN_LLM_API_KEY=sk-xxx
MN_LLM_MODEL=gpt-4o
MN_LLM_BASE_URL=https://api.openai.com/v1

# Or use a third-party OpenAI-compatible API
MN_LLM_PROVIDER=openai
MN_LLM_API_KEY=sk-xxx
MN_LLM_MODEL=deepseek-chat
MN_LLM_BASE_URL=https://api.deepseek.com/v1
```

### Quality Verification

Run the helper tool to check LLM connectivity and response quality:

```bash
python scripts/llm_check.py
```

### Script Quality Self-Check

After generating a script, check `output_dir/script.md`:

- Does the first sentence have a hook (question / exclamation / suspense, not a flat plot summary)?
- Is each sentence within `prompt_max_chars_per_sentence` limit (overlong sentences get hard-truncated, breaking semantics)?
- Does the overall structure have a narrative arc (not uniform information listing)?
- Are "signature scene" keywords matched (check `metadata.json` `match_summary`)?

### Cost Control

- Use weaker models for dev/debug, switch to strong models for production. TTS cache is unaffected by LLM switching.
- `research_max_tokens` controls research phase token cost; `prompt_target_sentences` controls script segment count.
- With pay-per-use APIs, a single 60s video run costs ~$0.01-0.05 (GPT-4o).

---

## BGM Selection

BGM is 50% of the listening experience. A royalty-free BGM with clean beats and a frequency band that doesn't compete with vocals beats any duck parameter tuning.

### Selection Criteria

| Dimension | Recommended | Avoid |
|-----------|-------------|-------|
| BPM | 90-130 (fast-cut); 60-80 (long narration) | <60 (sluggish) or >140 (anxious) |
| Frequency | Mid-low dominant, minimal highs | Vocal band (200Hz-4kHz) energy concentrated |
| Structure | Clear dynamic sections | Uniform throughout (no emotional curve possible) |
| Duration | >= target output x 1.2 | Shorter than output (loop splicing has seams) |
| License | Royalty-free / licensed | Pop songs (copyright risk) |

### BGM Analysis

Run the helper tool to analyze BGM characteristics:

```bash
python scripts/bgm_analyze.py /path/to/bgm.mp3
```

Output includes: duration, estimated BPM, energy distribution, preset suitability.

### Duck Parameter Tuning

After selecting BGM, fine-tune ducking per preset:

| Preset | Recommended duck_db | Notes |
|--------|---------------------|-------|
| douyin-fast | -10 to -12 | Fast-cut rhythm, BGM present but must not cover vocals |
| mainstream-dry | -14 to -16 | Slow rhythm, BGM as background ambience |
| bilibili-long | -16 to -20 | Long narration focus, very light BGM |

If BGM has high vocal-band energy, lower duck by an additional 2-3 dB.

### Royalty-Free BGM Sources

- YouTube Audio Library (free)
- Pixabay Music (free, no attribution required)
- Epidemic Sound (paid subscription)
- Artlist (paid subscription)

---

## Genre Routing

Different film genres need different presets and highlight strategies. One douyin-fast for everything makes comedies not funny enough, thrillers not tense enough, and dramas too rushed.

### Genre to Preset Mapping

| Genre | Recommended Preset | Parameter Adjustments | Rationale |
|-------|--------------------|-----------------------|-----------|
| Action / Sci-Fi | douyin-fast | `match_speed_clamp_max: 1.35` | Fast-cut suits high action density |
| Comedy | douyin-fast | `prompt_hook_seconds: 4` | Comedy needs setup time for punchlines |
| Mystery / Thriller | mainstream-dry | `match_timeline_mode: weighted_acts`, increase climax act weight | Thriller highlights concentrate in later acts |
| Horror | mainstream-dry | `bgm_duck_db: -14`, `tts_pause_ms: 300` | Horror needs whitespace and pauses for tension |
| Drama / Romance | bilibili-long | `prompt_target_segment_duration: 8.0` | Long takes, long narration, emotional buildup |
| Documentary | bilibili-long | Keep defaults | Even info density, suits long-form narration |
| Animation | douyin-fast | `match_speed_clamp_min: 0.9` | Animation has high visual info density, avoid over-slowdown |

### Using the Helper Tool

```bash
python scripts/genre_advisor.py --genre action --duration 60
```

Outputs recommended preset and parameter overrides.

### Custom Presets

If none of the three built-in presets fit, override parameters via YAML:

```yaml
# job.custom.yaml
narration_preset: mainstream-dry
params:
  match_speed_clamp_min: 0.8
  match_speed_clamp_max: 1.35
  bgm_duck_db: -12.0
  prompt_target_sentences: 15
  prompt_target_segment_duration: 4.0
  hook_templates:
    - "You won't guess this plot twist"
    - "Watch closely, this man is about to change everything"
```

---

## Narrative Focus

This is an information architecture problem, not an editing problem. Trying to cover an entire film in 60 seconds = time-lapse browsing = viewers swipe away.

### Core Principle

| Duration | Information Strategy | Narrative Structure |
|----------|---------------------|---------------------|
| 30s | Pure hook (1 signature scene + 1 suspense line) | Single-point explosion |
| 60s | One selling point (twist / signature scene / character arc) | Hook -> Setup -> Climax -> Resolution |
| 120s | Three-act micro-narrative | Setup -> Development -> Turn -> Resolution |

### `--style` Writing Guide

`--style` is not a genre label — it's "what this video sells".

| Writing | Effect | Issue |
|---------|--------|-------|
| `--style "hot and funny"` | Vague, LLM free-associates | Scattered info, no focus |
| `--style "only the final twist"` | Focuses on twist, everything else is setup | High completion rate |
| `--style "character's descent into darkness"` | Clear character arc | Suits 120s |
| `--style "signature scene roundup: 3 fight scenes"` | High highlight density | Suits action films |
| `--style "explain the timeline (non-linear narrative film)"` | High info value | Suits mystery films |

### Practical Advice

1. Before running, decide: what will viewers remember after watching?
2. If the answer is "nothing", the info is too scattered — narrow the scope.
3. The more specific `--style` is, the higher the LLM beats quality and match hit rate.
4. If a film has multiple selling points, make multiple videos — don't cram them into one.

---

## Publishing

Titles, thumbnails, and the first 1 second of motion determine completion rate on platforms. The engine produces a "publishable" video, but "going viral" requires publishing packaging.

### Title

The engine-generated `script.md` first sentence is a hook, but the publish title needs separate writing.

| Title Type | Example | Best For |
|-----------|---------|----------|
| Suspense | "99% of people didn't understand this ending" | Mystery / twist films |
| Emotional | "Cried for 3 days, this is the pinnacle of romance" | Romance / drama |
| Numeric | "3 min recap of this year's 5 most explosive fights" | Action / sci-fi |
| Controversial | "Everyone says it's bad, I'll tell you why it's great" | Controversial films |
| Identity | "Watch closely, this man is called Handsome" | General (Douyin style) |

Don't just use the film name as the title. The film name goes in the title, but it's not the title itself.

### Thumbnail

The engine auto-exports `cover.jpg` (highest-scored shot midpoint + film name overlay).

Thumbnail tips:

- Text no more than 8 characters, large font
- Choose the frame with the most dramatic facial expression
- For portrait publishing, use 9:16 ratio (auto-adapted with `--format 9:16`)
- Use Canva / CapCut for secondary text and sticker overlays

### First 1 Second

The first second determines whether viewers swipe away. The `render_title_card_sec` parameter controls intro title card duration.

| Preset | title_card_sec | Advice |
|--------|----------------|--------|
| douyin-fast | 1.0 | Title card + first hook sentence simultaneously |
| mainstream-dry | 0 | Go straight into footage |
| bilibili-long | 1.2 | Slightly longer title card, brand feel |

For a stronger first-second impact, write more powerful hooks in `hook_templates`.

### Publishing Checklist

- [ ] Title contains hook words (not just film name)
- [ ] Thumbnail has facial expression / action tension
- [ ] Thumbnail text <= 8 characters
- [ ] First 3 seconds have simultaneous audio + visual impact
- [ ] No black frames / silence segments (check `metadata.json` QA results)
- [ ] Subtitles within safe area (portrait auto-handled)

---

## Golden Sample Regression

Generated videos can silently drift in quality across releases. Institutionalize a golden-sample regression so quality changes are caught *before* shipping, not after users report them.

### When to Run

- **Before every release** (at tag cut).
- **On any PR that touches** `render`, `match`, `bgm`, `script`, or `tts`.

### Sample Matrix

| ID | Source | Aspect | Language | Why |
|----|--------|--------|----------|-----|
| G1 | Trailer / HD film | 16:9 | Chinese | Primary use case |
| G2 | Trailer / HD film | 16:9 | English | Translation + TTS path |
| G3 | Trailer / HD film | 9:16 | Chinese | Portrait publishing path |

Archive each run under `output/l2-runs/<date>-<sample>-<sha>/` (local, gitignored) so trends stay comparable across versions.

### Pass / Fail Thresholds

Carried from the L2 hand-test acceptance (`§B.3.5`):

- `match_summary.heuristic_ratio` ≤ 0.5
- `match_summary.scenes_after_drop` ≥ 3
- `speed_factor` must **not** be pinned at the clamp boundary (not exactly `match_speed_clamp_max` / `min`)

### Tools

- `scripts/compare_runs.py` — diff two `metadata.json` files (baseline vs new) for manual QA.
- `scripts/match_trend.py` — scan all `output/l2-runs/*/metadata.json` and print a `heuristic_ratio` / `embedding_ratio` / `score.avg` / `speed_factor.avg` trend table; alerts when `heuristic_ratio` regresses by more than `0.1` between consecutive runs.

```bash
python scripts/match_trend.py --root output/l2-runs --warn-delta 0.1
```

Any run that pushes `heuristic_ratio` above threshold (or above the previous release's value by > 0.1) must be investigated before the release ships.

---

## Quick Decision Tree

```
Output not good enough?
  |- Blurry footage / poor audio? -> Source Selection: change source
  |- Boring script / weak hook? -> LLM Selection: change LLM + Narrative Focus: focus selling point
  |- BGM covers vocals / doesn't fit? -> BGM Selection: change BGM
  |- Wrong rhythm / genre mismatch? -> Genre Routing: change preset
  |- Low completion rate? -> Narrative Focus + Publishing: focus + packaging
  +- All above checked and still not good? -> Tweak code params / enable VLM
```

---

## Helper Tools

| Tool | Purpose | Section |
|------|---------|---------|
| `scripts/source_check.py` | Check source quality (resolution / audio / duration) | Source Selection |
| `scripts/llm_check.py` | Check LLM connectivity and response quality | LLM Selection |
| `scripts/bgm_analyze.py` | Analyze BGM characteristics (BPM / energy / duration) | BGM Selection |
| `scripts/genre_advisor.py` | Recommend preset and parameters by genre | Genre Routing |
| `scripts/compare_runs.py` | Diff two `metadata.json` files (baseline vs new) for manual QA | Golden Sample Regression |
| `scripts/match_trend.py` | Trend analysis across regression runs (heuristic_ratio / embedding_ratio) | Golden Sample Regression |

All tools are standalone scripts with no `movie_narrator` package dependency:

```bash
python scripts/source_check.py /path/to/video.mp4
python scripts/bgm_analyze.py /path/to/bgm.mp3
python scripts/genre_advisor.py --genre action --duration 60
python scripts/llm_check.py
```

---

## Encoder Benchmark

If you have a GPU and want to decide between `render_encoder: auto` and `render_encoder: cpu`, benchmark your machine instead of guessing. `benchmarks/encoder_benchmark.py` generates a 5-second synthetic 1080p clip (no sample media needed) and encodes it with `libx264` plus every GPU encoder your ffmpeg reports (detected with the same logic the render pipeline uses; in CI environments GPU detection is skipped automatically).

```bash
python benchmarks/encoder_benchmark.py                # print a comparison table
python benchmarks/encoder_benchmark.py --out gpu.json # also write the JSON report
mn benchmark                                          # same benchmark from the CLI (v1.4.2; also: --duration, --encoders libx264,nvenc)
```

How to read the numbers:

- **wall s** — total wall-clock encode time; lower is faster.
- **fps** — ffmpeg's reported encode throughput (frames per second); the headline number for speed comparisons.
- **size MB** — output file size; GPU encoders at fixed quality settings usually produce somewhat larger files than CRF-20 x264. A huge size gap signals the quality settings are not equivalent.
- **status** — `failed` rows (e.g. NVENC listed by ffmpeg but no usable GPU hardware, VAAPI without a device) are recorded, not fatal: treat that backend as unavailable on your machine.

Rule of thumb: if the GPU encoder's `fps` is at least 2-3x `libx264` at a comparable size, use `render_encoder: auto`; otherwise stay on `cpu`.

---

## Timeline Export (NLE Hand-off)

The AI edit is a first draft. If you want to fine-tune it by hand in an NLE instead of publishing the render as-is, enable the `timeline_export` plugin and pick a backend with the `timeline_export_backend` job parameter — the step runs after `render_video` and writes an edit draft under `output/<movie>/timeline/`.

```bash
cd examples/plugins/timeline_export
pip install -e .            # base plugin (Jianying backend, no extra deps)
pip install -e ".[otio]"    # + OpenTimelineIO backend
```

| Backend | Output | Import into |
|---------|--------|-------------|
| `jianying` (default) | `draft_content.json` draft bundle | Jianying / CapCut (domestic) |
| `otio` | `<movie>.otio` | DaVinci Resolve, in-house OTIO tooling |
| `premiere` | `<movie>.xml` | Adobe Premiere Pro — `File > Import…` |

The `premiere` backend writes a Final Cut Pro 7 XML sequence: matched source clips on the video track, subtitle/title/watermark cards as text generators on a second track, and the narration stem (the final mix when BGM ran) on an audio track. It needs no optional dependency.

```yaml
params:
  timeline_export_backend: premiere
```

Then in Premiere: `File > Import…`, pick `output/<movie>/timeline/<movie>.xml`, and the sequence appears in your project panel with clips already cut and placed — reorder, trim, swap shots, adjust the text, and export from there. Values are best-effort interchange data (frame-accurate at the sequence rate read from your render settings); treat the draft as a starting point, not a final conform.

---

## 4K & 10-bit Rendering

By default the render produces a 1080p 8-bit (`yuv420p`) SDR stream. Two job parameters switch the pixel pipeline (v1.5.0, ADR-017): `video_sizes` + `video_format` set the output size, `render_bit_depth: 10` switches the encode to 10-bit (`yuv420p10le` + libx264 `high10`), and `render_color_space: hdr10` writes BT.2020/PQ color tags (and auto-forces 10-bit).

What to expect before you enable it:

- **Encode time** — budget roughly 3-4x the encode time of the same job at 1080p 8-bit: 4x the pixels, and 10-bit carries ~25% more data per frame on top. Consider a faster `render_preset` than `slow`, and run a short `render_preview_mode` pass first.
- **Temp disk** — the render admission pre-flight (opt-in via `MN_ADMISSION_DISK_CHECK`) applies a x1.25 factor to its temp-space estimate for 10-bit renders. Make sure the output volume has real headroom; 4K intermediates are large.
- **10-bit is CPU-only** — the supported H.264 GPU encoders (NVENC / VAAPI / VideoToolbox) are 8-bit only. With `render_encoder: auto`, a 10-bit render falls back to libx264 (CPU) and metadata records the reason as `10bit_gpu_unsupported` — the output is identical, just slower. GPU 10-bit (HEVC main10) is future work.
- **HDR10 is tag-level** — v1.5.0 writes the color tags (`bt2020nc` / `smpte2084`), not mastering-display metadata (MaxCLL/MaxFALL SEI is out of scope). Players that ignore the tags render washed-out colors — treat `hdr10` as a mastering/archival hand-off option, not a publishing default.

Recommended job snippets:

```yaml
# 4K 10-bit SDR (landscape)
params:
  video_sizes:
    "16:9": [3840, 2160]
    "9:16": [2160, 3840]
  video_format: "16:9"
  render_bit_depth: 10
```

```yaml
# HDR10 (10-bit is forced automatically)
params:
  render_color_space: hdr10
```

The QA step cross-checks the deliverable against these settings: a 4K-class request (>= 3840 wide or >= 2160 tall, either orientation) must be reproduced exactly, and the probed `pix_fmt` / `color_transfer` must match the requested bit depth / color space — mismatches are reported under `video_qa` in `metadata.json` (see [METADATA_SCHEMA.md](METADATA_SCHEMA.md)).
