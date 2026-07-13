from pathlib import Path

from ..models import Context, SubtitlePaths


def _format_time(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hrs, rem = divmod(total_ms, 3_600_000)
    mins, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def _write_srt(path: Path, cues: list[tuple[int, str, str, str]]) -> None:
    """Write a SRT file with explicit LF between cue body lines and cues.

    Per multi-language-subtitle-design.md §7.2 step 2: bilingual cues
    join original + translation with `\n` (LF, never CRLF) for tooling
    predictability. Cue-block separator is also LF (blank line = LF).
    File is opened with `newline=""` so Python does not translate the
    LF bytes we wrote on Windows.
    """
    with open(path, "w", encoding="utf-8", newline="") as f:
        for idx, start, end, body in cues:
            f.write(f"{idx}\n")
            f.write(f"{start} --> {end}\n")
            f.write(f"{body}\n\n")


def _lang_tag_for_filename(lang: str) -> str:
    """BCP-47 lite → filesystem-safe tag. Lowercase, hyphens preserved.

    Examples: `en` → `en`, `zh-TW` → `zh-tw`, `en-US` → `en-us`.
    """
    return lang.strip().lower()


def _resolve_render_subtitle_path(
    ctx: Context, paths: SubtitlePaths
) -> str | None:
    """Pick the render-overlay subtitle path per `subtitle_mode` (spec §7.2 step 4).

    `mode == "original"` always picks the original. For `translated` /
    `bilingual`, if the requested track is missing we fall back to the
    original and surface an inline_warn.
    """
    mode = ctx.metadata.get("subtitle_mode", "original")
    if mode == "translated":
        if paths.translated:
            return paths.translated
        if ctx.services and ctx.services.console:
            ctx.services.console.inline_warn(
                "subtitle_mode=translated but translated track is missing; using original"
            )
        return paths.original
    if mode == "bilingual":
        if paths.bilingual:
            return paths.bilingual
        if ctx.services and ctx.services.console:
            ctx.services.console.inline_warn(
                "subtitle_mode=bilingual but bilingual track is missing; using original"
            )
        return paths.original
    # Default / explicit original
    return paths.original


def generate_subtitle(ctx: Context) -> Context:
    """Write SRT files and populate `subtitle_paths` / `render_subtitle_path`.

    Always writes `subtitle.srt` from `timed_segments`. When
    `translated_texts` is non-empty and length-aligned, also writes
    `subtitle.<lang>.srt` and `subtitle.bilingual.srt` (cues joined by
    LF). Sets `subtitle_path` to the original (invariant) and
    `render_subtitle_path` to the mode-selected track.
    """
    output_dir = Path(ctx.output_dir)
    original_path = output_dir / "subtitle.srt"
    translated_path: Path | None = None
    bilingual_path: Path | None = None

    # 1. Always write the original SRT.
    cues: list[tuple[int, str, str, str]] = [
        (i, _format_time(seg.start), _format_time(seg.end), seg.text)
        for i, seg in enumerate(ctx.timed_segments, 1)
    ]
    _write_srt(original_path, cues)
    ctx.subtitle_path = str(original_path)

    # 2. Translated + bilingual — only when both sides are aligned.
    has_translations = (
        bool(ctx.translated_texts)
        and len(ctx.translated_texts) == len(ctx.timed_segments)
    )
    if has_translations:
        target_lang = ctx.metadata.get("subtitle_lang") or "translated"
        tag = _lang_tag_for_filename(target_lang)
        translated_path = output_dir / f"subtitle.{tag}.srt"
        bilingual_path = output_dir / "subtitle.bilingual.srt"

        translated_cues: list[tuple[int, str, str, str]] = [
            (i, _format_time(seg.start), _format_time(seg.end), tr)
            for i, (seg, tr) in enumerate(
                zip(ctx.timed_segments, ctx.translated_texts), 1
            )
        ]
        bilingual_cues: list[tuple[int, str, str, str]] = [
            (i, _format_time(seg.start), _format_time(seg.end), f"{seg.text}\n{tr}")
            for i, (seg, tr) in enumerate(
                zip(ctx.timed_segments, ctx.translated_texts), 1
            )
        ]
        _write_srt(translated_path, translated_cues)
        _write_srt(bilingual_path, bilingual_cues)

    # 3. Bundle paths into SubtitlePaths and resolve render track.
    paths = SubtitlePaths(
        original=str(original_path),
        translated=str(translated_path) if translated_path else None,
        bilingual=str(bilingual_path) if bilingual_path else None,
    )
    ctx.subtitle_paths = paths
    ctx.render_subtitle_path = _resolve_render_subtitle_path(ctx, paths)
    return ctx
