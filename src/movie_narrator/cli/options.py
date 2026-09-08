# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared ``Annotated`` CLI option aliases for Movie Narrator.

These aliases centralize options that are repeated across the ``create``,
``race``, ``imitate`` and ``submit`` commands in :mod:`movie_narrator.cli`.
Each alias bundles the flag list and help text so the same flag is declared
only once.  Intentional per-command differences are expressed as distinct
aliases (e.g. ``MovieOpt`` vs ``MovieRequired``, the four ``OutputDirXxx``
variants, and ``VoiceWithSign`` vs ``VoicePlain``).

Note: with this typer version (0.26.x) an ``Annotated`` option's first
positional argument to ``typer.Option`` is interpreted as a flag name, not a
default value.  A default value is therefore provided at the call site via the
parameter's ``= <default>`` in each command signature.  Only required options
such as ``MovieRequired`` carry ``...`` here and need no signature default.
"""

from typing import Annotated, Optional

import typer


# --movie / -m: movie name.  Optional in create/race/imitate, required in submit.
MovieOpt = Annotated[
    Optional[str],
    typer.Option("--movie", "-m", help="电影名称 / Movie name"),
]

MovieRequired = Annotated[
    str,
    typer.Option(..., "--movie", "-m", help="电影名称 / Movie name"),
]

# --style / -s
StyleOpt = Annotated[
    str,
    typer.Option("--style", "-s", help="解说风格 / Narration style"),
]

# --duration / -d
DurationOpt = Annotated[
    int,
    typer.Option("--duration", "-d", help="目标时长(秒) / Target duration (seconds)"),
]

# --voice / -v: two intentional help variants (create/race mention Edge TTS).
VoiceWithSign = Annotated[
    Optional[str],
    typer.Option("--voice", "-v", help="TTS 语音 / TTS voice (Edge TTS)"),
]

VoicePlain = Annotated[
    Optional[str],
    typer.Option("--voice", "-v", help="TTS 语音 / TTS voice"),
]

# --video-format / --format / -f
VideoFormatOpt = Annotated[
    str,
    typer.Option(
        "--video-format",
        "--format",
        "-f",
        help="视频格式 16:9 或 9:16 / Video format: 16:9 or 9:16",
    ),
]

# --video
VideoOpt = Annotated[
    Optional[str],
    typer.Option("--video", help="源视频文件路径 / Source movie file path"),
]

# --library-dir
LibraryDirOpt = Annotated[
    Optional[str],
    typer.Option("--library-dir", help="影视库目录 / Movie library directory"),
]

# --research / --no-research
ResearchOpt = Annotated[
    Optional[bool],
    typer.Option("--research/--no-research", help="启用剧情研究 / Enable plot research"),
]

# --bgm
BgmOpt = Annotated[
    Optional[str],
    typer.Option("--bgm", help="背景音乐文件 / Background music file"),
]

# --no-bgm
NoBgmOpt = Annotated[
    bool,
    typer.Option("--no-bgm", help="禁用 BGM / Disable BGM even if default set"),
]

# --keep-cache
KeepCacheOpt = Annotated[
    bool,
    typer.Option("--keep-cache", help="保留 TTS 缓存 / Keep TTS cache files"),
]

# --no-clips
NoClipsOpt = Annotated[
    bool,
    typer.Option("--no-clips", help="跳过片段导出 / Skip clips/export"),
]

# --strict
StrictOpt = Annotated[
    bool,
    typer.Option("--strict", help="软步骤失败即中止 / Abort on soft step failure"),
]

# --retry
RetryOpt = Annotated[
    bool,
    typer.Option(
        "--retry",
        help="硬步骤失败时交互重试 / Enable interactive retry on hard step failure",
    ),
]

# --config
ConfigOpt = Annotated[
    Optional[str],
    typer.Option("--config", help="job YAML 配置路径 / Path to job YAML config"),
]

# --subtitle-lang
SubtitleLangOpt = Annotated[
    Optional[str],
    typer.Option(
        "--subtitle-lang",
        help="目标语言标签(如 en, ja, zh-TW) / Target language tag; empty = off",
    ),
]

# --subtitle-mode
SubtitleModeOpt = Annotated[
    Optional[str],
    typer.Option(
        "--subtitle-mode",
        help="字幕模式 original|translated|bilingual / Overlay mode",
    ),
]

# --narration-preset / -p / --preset
NarrationPresetOpt = Annotated[
    Optional[str],
    typer.Option(
        "--narration-preset",
        "-p",
        "--preset",
        help="解说风格预设(内置或已安装社区预设) douyin-fast | mainstream-dry | bilibili-long "
        "/ Narration style preset (built-in or installed community preset)",
    ),
]

# --output-dir / -o: four intentional default-hint variants.
OutputDirCreate = Annotated[
    Optional[str],
    typer.Option(
        "--output-dir",
        "-o",
        help="输出目录(默认 output/<电影名>) / Output directory (default: output/<movie>)",
    ),
]

OutputDirRace = Annotated[
    Optional[str],
    typer.Option(
        "--output-dir",
        "-o",
        help="输出目录(默认 output/<电影名>_race) / Output directory",
    ),
]

OutputDirImitate = Annotated[
    Optional[str],
    typer.Option(
        "--output-dir",
        "-o",
        help="输出目录(默认 output/<电影名>_imitate) / Output directory",
    ),
]

OutputDirPlain = Annotated[
    Optional[str],
    typer.Option("--output-dir", "-o", help="输出目录 / Output directory"),
]

# --dry-run (create only)
DryRunOpt = Annotated[
    bool,
    typer.Option(
        "--dry-run",
        help="生成前预览：仅产出研究/分镜/脚本，不调用 TTS 与 FFmpeg "
        "(不生成 final.mp4) / Dry-run: script/storyboard only, no TTS or render",
    ),
]