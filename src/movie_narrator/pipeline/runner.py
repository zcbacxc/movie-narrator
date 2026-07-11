from pathlib import Path

from ..models import Context
from .script import generate_script
from .tts import generate_voice
from .subtitle import generate_subtitle
from .render import render_video

STEPS = [
    generate_script,
    generate_voice,
    generate_subtitle,
    render_video,
]


def run_pipeline(
    movie: str,
    style: str,
    duration: int,
    voice: str | None,
    format: str,
    output_dir: Path,
    keep_cache: bool = False,
) -> Path:
    ctx = Context(
        movie_name=movie,
        style=style,
        duration=duration,
        output_dir=str(output_dir),
    )
    ctx.metadata["voice"] = voice
    ctx.metadata["format"] = format
    ctx.metadata["keep_cache"] = keep_cache

    for step in STEPS:
        name = step.__name__
        print(f"▶ {name}")
        try:
            ctx = step(ctx)
            print(f"✓ {name}")
        except Exception as e:
            print(f"✗ {name}: {e}")
            raise

    return Path(ctx.video_path)
