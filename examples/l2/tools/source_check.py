#!/usr/bin/env python3
"""Q-X1 辅助工具 — 源片质量检查器。

检查源视频文件的分辨率、音轨、时长、码率等指标，
给出「源片就绪度」评分和改进建议。

用法:
    python source_check.py /path/to/video.mp4
    python source_check.py /path/to/video.mp4 --target-duration 60

依赖: ffprobe (随 ffmpeg 安装)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VideoInfo:
    width: int
    height: int
    duration_sec: float
    has_audio: bool
    audio_codec: str | None
    video_codec: str | None
    bitrate_kbps: int | None
    fps: float | None


def run_ffprobe(video_path: str) -> VideoInfo:
    """Run ffprobe and parse stream info."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"ERROR: ffprobe failed: {result.stderr.strip()}")
        sys.exit(1)

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    format_info = data.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not video_stream:
        print("ERROR: No video stream found")
        sys.exit(1)

    # Parse FPS (e.g. "30000/1001" -> 29.97)
    fps_str = video_stream.get("r_frame_rate", "0/1")
    try:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else None
    except (ValueError, ZeroDivisionError):
        fps = None

    duration = float(format_info.get("duration", 0))
    bitrate = int(format_info.get("bit_rate", 0)) // 1000 if format_info.get("bit_rate") else None

    return VideoInfo(
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        duration_sec=duration,
        has_audio=audio_stream is not None,
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        video_codec=video_stream.get("codec_name"),
        bitrate_kbps=bitrate,
        fps=fps,
    )


def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def assess_quality(info: VideoInfo, target_duration: int = 60) -> list[tuple[str, str, str]]:
    """Return list of (check, status, detail) tuples."""
    checks: list[tuple[str, str, str]] = []

    # Resolution
    min_dim = min(info.width, info.height)
    if min_dim >= 1080:
        checks.append(("分辨率", "PASS", f"{info.width}x{info.height} — 1080p+，画质优秀"))
    elif min_dim >= 720:
        checks.append(("分辨率", "WARN", f"{info.width}x{info.height} — 720p，竖屏裁切后可能模糊"))
    else:
        checks.append(("分辨率", "FAIL", f"{info.width}x{info.height} — 低于 720p，不建议使用"))

    # Audio track
    if info.has_audio:
        checks.append(("音轨", "PASS", f"codec={info.audio_codec} — 有音轨，WhisperX 可用"))
    else:
        checks.append(("音轨", "FAIL", "无音轨 — WhisperX 将失败，match 全部回退 heuristic"))

    # Duration vs target
    min_source_duration = target_duration * 3
    if info.duration_sec >= min_source_duration:
        checks.append(("时长", "PASS", f"{format_duration(info.duration_sec)} — 足够制作 {target_duration}s 成片"))
    elif info.duration_sec >= target_duration * 2:
        checks.append(("时长", "WARN", f"{format_duration(info.duration_sec)} — 勉强够 {target_duration}s，建议更长的源片"))
    else:
        checks.append(("时长", "FAIL", f"{format_duration(info.duration_sec)} — 源片过短，至少需要 {min_source_duration}s"))

    # Bitrate
    if info.bitrate_kbps:
        if info.bitrate_kbps >= 4000:
            checks.append(("码率", "PASS", f"{info.bitrate_kbps} kbps — 高码率，画质好"))
        elif info.bitrate_kbps >= 2000:
            checks.append(("码率", "WARN", f"{info.bitrate_kbps} kbps — 中等码率，可用"))
        else:
            checks.append(("码率", "FAIL", f"{info.bitrate_kbps} kbps — 低码率，画面可能有压缩伪影"))
    else:
        checks.append(("码率", "WARN", "无法读取码率信息"))

    # Codec
    if info.video_codec in ("h264", "hevc", "av1"):
        checks.append(("编码", "PASS", f"{info.video_codec} — 现代编码，兼容性好"))
    else:
        checks.append(("编码", "WARN", f"{info.video_codec} — 可能需要转码"))

    # FPS
    if info.fps:
        if 23 <= info.fps <= 30:
            checks.append(("帧率", "PASS", f"{info.fps:.1f} fps — 电影标准帧率"))
        elif info.fps > 30:
            checks.append(("帧率", "WARN", f"{info.fps:.1f} fps — 高帧率，引擎会降至 24fps 渲染"))
        else:
            checks.append(("帧率", "WARN", f"{info.fps:.1f} fps — 低帧率，可能卡顿"))
    else:
        checks.append(("帧率", "WARN", "无法读取帧率"))

    return checks


def main():
    if len(sys.argv) < 2:
        print("用法: python source_check.py <video_path> [--target-duration N]")
        sys.exit(1)

    video_path = sys.argv[1]
    target_duration = 60

    if "--target-duration" in sys.argv:
        idx = sys.argv.index("--target-duration")
        target_duration = int(sys.argv[idx + 1])

    if not Path(video_path).exists():
        print(f"ERROR: File not found: {video_path}")
        sys.exit(1)

    if not shutil.which("ffprobe"):
        print("ERROR: ffprobe not found. Install ffmpeg first.")
        sys.exit(1)

    print(f"检查源片: {video_path}")
    print(f"目标成片时长: {target_duration}s")
    print()

    info = run_ffprobe(video_path)
    checks = assess_quality(info, target_duration)

    pass_count = sum(1 for _, s, _ in checks if s == "PASS")
    warn_count = sum(1 for _, s, _ in checks if s == "WARN")
    fail_count = sum(1 for _, s, _ in checks if s == "FAIL")

    print(f"{'检查项':<8} {'状态':<6} 详情")
    print("-" * 60)
    for name, status, detail in checks:
        icon = {"PASS": "[OK]", "WARN": "[!!]", "FAIL": "[XX]"}[status]
        print(f"{name:<8} {icon:<6} {detail}")

    print()
    total = len(checks)
    score = (pass_count * 100 + warn_count * 50) // (total * 100) if total > 0 else 0
    print(f"就绪度: {pass_count}/{total} PASS, {warn_count} WARN, {fail_count} FAIL")

    if fail_count > 0:
        print("\n建议: 修复 FAIL 项后再跑片，否则成片质量会受严重影响。")
    elif warn_count > 0:
        print("\n建议: WARN 项不影响跑片，但建议优化以获得更好效果。")
    else:
        print("\n源片质量优秀，可以开始跑片。")


if __name__ == "__main__":
    main()
