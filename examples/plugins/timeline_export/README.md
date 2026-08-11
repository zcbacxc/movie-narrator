# movie-narrator-timeline-export

A [movie-narrator](https://github.com/zcbacxc/movie-narrator) plugin that
exports the current edit as a timeline draft for manual fine-tuning in an
NLE (non-linear editor). It fills the "AI output → human polish" gap for
Chinese creators who want to hand-tweak a generated recap in Jianying
(CapCut domestic) or a pro editor that speaks OpenTimelineIO.

## Installation

```bash
cd examples/plugins/timeline_export
pip install -e .            # base plugin (Jianying backend)
pip install -e ".[otio]"    # + OTIO backend (requires opentimelineio)
```

After installation the plugin is auto-discovered via the
`movie_narrator.plugins` entry point.

## Usage

```bash
# Enable the step and pick a backend (default: jianying)
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60 \
    --param timeline_export_backend=otio
```

The step runs after `render_video` and writes a timeline draft under
`output/<movie>/timeline/`:

- `otio`    → `<movie>.otio`
- `jianying` → `<movie>_jianying/draft_content.json` + `draft_meta_info.json`

## Backends

| Backend | Interchange | License | Notes |
|---------|-------------|---------|-------|
| `jianying` (default) | Jianying draft JSON | self-authored | Best-effort; the format is unpublished and owned by ByteDance, may drift |
| `otio`    | OpenTimelineIO `.otio` | Apache-2.0 | Professional standard; preferred for interoperability |

> **License note**: the Jianying draft generator is written from scratch by
> this project. It does **not** copy TypeTale or any other implementation.
> The draft schema is a ByteDance-proprietary, unpublished format; the
> produced JSON is best-effort and may need adjustment if the format
> changes. Prefer OTIO for reliable interchange.

## Job parameter

- `timeline_export_backend`: `otio` | `jianying` (default `jianying`)

## Development

```bash
pip install -e ".[otio]"
python -c "from movie_narrator_timeline_export import TimelineExportPlugin; print(TimelineExportPlugin.name)"
```