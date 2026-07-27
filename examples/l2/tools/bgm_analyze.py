#!/usr/bin/env python3
"""Q-X3 辅助工具 — BGM 特征分析器。

分析 BGM 文件的时长、估算 BPM、能量分布，
判断是否适合当前 preset，给出 duck 参数建议。

用法:
    python bgm_analyze.py /path/to/bgm.mp3
    python bgm_analyze.py /path/to/bgm.mp3 --preset douyin-fast --target-duration 60

依赖: ffprobe (随 ffmpeg 安装)
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path


def run_ffprobe(audio_path: str) -> dict:
    """Run ffprobe and return parsed info."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"ERROR: ffprobe failed: {result.stderr.strip()}")
        sys.exit(1)
    return json.loads(result.stdout)


def estimate_bpm(audio_path: str, duration_sec: float) -> float | None:
    """Estimate BPM using ffmpeg's silencedetect + onset heuristic.

    This is a lightweight heuristic — for precise BPM use librosa.
    """
    # Sample a 30-second segment from the middle for BPM estimation
    start = max(0, duration_sec / 2 - 15)
    sample_duration = min(30, duration_sec)

    # Use ffmpeg to extract loudness peaks as a proxy for beats
    cmd = [
        "ffmpeg", "-v", "quiet",
        "-ss", str(start), "-t", str(sample_duration),
        "-i", audio_path,
        "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    # Parse RMS level changes to estimate beat frequency
    lines = result.stderr.split("\n")
    rms_values: list[float] = []
    for line in lines:
        if "lavfi.astats.Overall.RMS_level=" in line:
            val_str = line.split("=")[-1].strip()
            try:
                rms_values.append(float(val_str))
            except ValueError:
                continue

    if len(rms_values) < 10:
        return None

    # Count zero-crossings of the mean-centered RMS as beat proxy
    mean_rms = sum(rms_values) / len(rms_values)
    crossings = 0
    above = rms_values[0] > mean_rms
    for val in rms_values[1:]:
        is_above = val > mean_rms
        if is_above != above:
            crossings += 1
            above = is_above

    # Each beat = 2 crossings (up + down). astats reset=1 gives ~10 samples/sec.
    samples_per_sec = 10  # approximate
    estimated_bpm = (crossings / 2) * (60 / sample_duration) * (samples_per_sec / samples_per_sec)

    # Clamp to reasonable range
    if estimated_bpm < 40:
        estimated_bpm *= 2  # likely half-time
    if estimated_bpm > 200:
        estimated_bpm /= 2  # likely double-time

    return round(estimated_bpm)


def get_energy_profile(audio_path: str, duration_sec: float) -> dict:
    """Get energy distribution across frequency bands."""
    # Analyze low (0-250Hz), mid (250-4kHz), high (4k+Hz) bands
    start = max(0, duration_sec / 2 - 15)
    sample_duration = min(30, duration_sec)

    bands = {"low": "lowpass=f=250", "mid": "bandpass=f=1500:width_type=h:w=2000", "high": "highpass=f=4000"}
    results: dict[str, float] = {}

    for band_name, filter_expr in bands.items():
        cmd = [
            "ffmpeg", "-v", "quiet",
            "-ss", str(start), "-t", str(sample_duration),
            "-i", audio_path,
            "-af", f"{filter_expr},volumedetect",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        # Parse mean_volume from stderr
        for line in result.stderr.split("\n"):
            if "mean_volume" in line:
                try:
                    val = float(line.split("=")[-1].strip().replace(" dB", ""))
                    results[band_name] = val
                    break
                except ValueError:
                    continue
        if band_name not in results:
            results[band_name] = -60.0  # silence fallback

    return results


def assess_suitability(
    duration_sec: float,
    bpm: float | None,
    energy: dict[str, float],
    preset: str,
    target_duration: int,
) -> list[tuple[str, str, str]]:
    """Assess BGM suitability for the given preset."""
    checks: list[tuple[str, str, str]] = []

    # Duration
    min_duration = target_duration * 1.2
    if duration_sec >= min_duration:
        checks.append(("时长", "PASS", f"{duration_sec:.0f}s — 足够覆盖 {target_duration}s 成片"))
    elif duration_sec >= target_duration:
        checks.append(("时长", "WARN", f"{duration_sec:.0f}s — 勉强够，可能需要循环拼接"))
    else:
        checks.append(("时长", "FAIL", f"{duration_sec:.0f}s — 短于成片 {target_duration}s，必须循环"))

    # BPM
    if bpm:
        if preset == "douyin-fast":
            if 90 <= bpm <= 130:
                checks.append(("BPM", "PASS", f"~{bpm} — 适合 douyin-fast 快剪节奏"))
            elif 70 <= bpm <= 140:
                checks.append(("BPM", "WARN", f"~{bpm} — 可用但节奏不完美，理想 90-130"))
            else:
                checks.append(("BPM", "FAIL", f"~{bpm} — 不适合快剪，BPM 过低或过高"))
        elif preset == "mainstream-dry":
            if 70 <= bpm <= 110:
                checks.append(("BPM", "PASS", f"~{bpm} — 适合 mainstream-dry 中速叙事"))
            else:
                checks.append(("BPM", "WARN", f"~{bpm} — 中速叙事理想 70-110"))
        elif preset == "bilibili-long":
            if 60 <= bpm <= 90:
                checks.append(("BPM", "PASS", f"~{bpm} — 适合 bilibili-long 慢节奏"))
            else:
                checks.append(("BPM", "WARN", f"~{bpm} — 长解说理想 60-90"))
    else:
        checks.append(("BPM", "WARN", "无法估算 BPM"))

    # Energy distribution — check if mid-band (vocal range) is too hot
    mid_energy = energy.get("mid", -60.0)
    low_energy = energy.get("low", -60.0)
    high_energy = energy.get("high", -60.0)

    if mid_energy > -20:
        checks.append(("频段", "WARN", f"中频(人声段)能量偏高({mid_energy:.0f}dB)，建议额外降 duck 2-3dB"))
    else:
        checks.append(("频段", "PASS", f"中频(人声段)能量正常({mid_energy:.0f}dB)，不抢人声"))

    if high_energy > -15:
        checks.append(("高频", "WARN", f"高频能量偏高({high_energy:.0f}dB)，可能有刺耳感"))
    else:
        checks.append(("高频", "PASS", f"高频能量正常({high_energy:.0f}dB)"))

    return checks


def recommend_duck(preset: str, energy: dict[str, float]) -> str:
    """Recommend duck_db based on BGM characteristics."""
    base = {"douyin-fast": -10.0, "mainstream-dry": -15.0, "bilibili-long": -18.0}
    duck = base.get(preset, -12.0)

    mid_energy = energy.get("mid", -60.0)
    if mid_energy > -20:
        duck -= 2.5  # Extra ducking for vocal-band-heavy BGM

    return f"{duck:.1f}"


def main():
    if len(sys.argv) < 2:
        print("用法: python bgm_analyze.py <bgm_path> [--preset P] [--target-duration N]")
        sys.exit(1)

    audio_path = sys.argv[1]
    preset = "douyin-fast"
    target_duration = 60

    if "--preset" in sys.argv:
        idx = sys.argv.index("--preset")
        preset = sys.argv[idx + 1]
    if "--target-duration" in sys.argv:
        idx = sys.argv.index("--target-duration")
        target_duration = int(sys.argv[idx + 1])

    if not Path(audio_path).exists():
        print(f"ERROR: File not found: {audio_path}")
        sys.exit(1)

    if not shutil.which("ffprobe"):
        print("ERROR: ffprobe not found. Install ffmpeg first.")
        sys.exit(1)

    print(f"分析 BGM: {audio_path}")
    print(f"目标 Preset: {preset}")
    print(f"目标成片时长: {target_duration}s")
    print()

    data = run_ffprobe(audio_path)
    duration_sec = float(data.get("format", {}).get("duration", 0))

    print("[1/3] 基础信息")
    print("-" * 40)
    audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio_stream:
        print(f"  编码: {audio_stream.get('codec_name', 'unknown')}")
        print(f"  采样率: {audio_stream.get('sample_rate', 'unknown')} Hz")
        print(f"  声道: {audio_stream.get('channels', 'unknown')}")
    print(f"  时长: {duration_sec:.1f}s")
    print()

    print("[2/3] BPM 估算与频段分析")
    print("-" * 40)
    bpm = estimate_bpm(audio_path, duration_sec)
    energy = get_energy_profile(audio_path, duration_sec)

    print(f"  估算 BPM: {bpm if bpm else '无法估算'}")
    print(f"  低频能量: {energy.get('low', -60):.1f} dB")
    print(f"  中频能量: {energy.get('mid', -60):.1f} dB")
    print(f"  高频能量: {energy.get('high', -60):.1f} dB")
    print()

    print("[3/3] 适配度评估")
    print("-" * 40)
    checks = assess_suitability(duration_sec, bpm, energy, preset, target_duration)

    pass_count = sum(1 for _, s, _ in checks if s == "PASS")
    warn_count = sum(1 for _, s, _ in checks if s == "WARN")
    fail_count = sum(1 for _, s, _ in checks if s == "FAIL")

    for name, status, detail in checks:
        icon = {"PASS": "[OK]", "WARN": "[!!]", "FAIL": "[XX]"}[status]
        print(f"  {name:<8} {icon:<6} {detail}")

    print()
    print(f"就绪度: {pass_count}/{len(checks)} PASS, {warn_count} WARN, {fail_count} FAIL")

    # Duck recommendation
    recommended_duck = recommend_duck(preset, energy)
    print(f"\n推荐参数:")
    print(f"  bgm_duck_db: {recommended_duck}")
    print(f"  (在 job.yaml 的 params 中设置)")

    if fail_count > 0:
        print("\n建议: BGM 存在严重问题，建议更换。")
    elif warn_count > 0:
        print("\n建议: BGM 可用但非最优，按推荐参数微调可改善效果。")
    else:
        print("\nBGM 质量优秀，适合当前 preset。")


if __name__ == "__main__":
    main()
