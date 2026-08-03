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
| `scripts/match_trend.py` | Trend analysis across regression runs (heuristic_ratio / embedding_ratio) | Golden Sample Regression |

All tools are standalone scripts with no `movie_narrator` package dependency:

```bash
python scripts/source_check.py /path/to/video.mp4
python scripts/bgm_analyze.py /path/to/bgm.mp3
python scripts/genre_advisor.py --genre action --duration 60
python scripts/llm_check.py
```
