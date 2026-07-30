# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

#!/usr/bin/env python3
"""Genre advisor — recommends preset and parameters by film genre.

根据电影类型推荐 preset 和参数覆盖，
生成可直接使用的 YAML 配置片段。

用法:
    python genre_advisor.py --genre 动作 --duration 60
    python genre_advisor.py --genre 悬疑 --duration 120 --format 9:16
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field


@dataclass
class GenreProfile:
    preset: str
    params: dict = field(default_factory=dict)
    hook_templates: list[str] = field(default_factory=list)
    style_hint: str = ""
    rationale: str = ""


# Genre → Profile mapping
GENRE_PROFILES: dict[str, GenreProfile] = {
    "动作": GenreProfile(
        preset="douyin-fast",
        params={
            "match_speed_clamp_min": 0.85,
            "match_speed_clamp_max": 1.35,
            "match_drop_scene_min_duration": 0.3,
            "bgm_duck_db": -10.0,
            "prompt_target_sentences": 18,
            "prompt_target_segment_duration": 3.3,
            "render_title_card_sec": 1.0,
        },
        hook_templates=[
            "这片打戏我看了十遍{movie}",
            "{movie}这段动作戏直接封神",
            "注意看，{movie}这段打斗太炸了",
        ],
        style_hint="名场面盘点：聚焦三场最炸打戏",
        rationale="动作片高光密度高，快切节奏最大化动作冲击力。放宽速度上限让快进更流畅。",
    ),
    "科幻": GenreProfile(
        preset="douyin-fast",
        params={
            "match_speed_clamp_min": 0.85,
            "match_speed_clamp_max": 1.30,
            "bgm_duck_db": -10.0,
            "prompt_target_sentences": 16,
            "prompt_target_segment_duration": 3.5,
            "render_title_card_sec": 1.2,
        },
        hook_templates=[
            "这部电影的世界观太震撼了{movie}",
            "{movie}的设定我敢说你没完全看懂",
            "未来真的会变成这样吗？{movie}",
        ],
        style_hint="聚焦世界观设定和核心科幻概念",
        rationale="科幻片需要解释设定，稍长句式。标题卡稍长增强氛围感。",
    ),
    "喜剧": GenreProfile(
        preset="douyin-fast",
        params={
            "match_speed_clamp_min": 0.90,
            "match_speed_clamp_max": 1.25,
            "bgm_duck_db": -10.0,
            "tts_pause_ms": 200,
            "prompt_hook_seconds": 4,
            "prompt_target_sentences": 18,
            "prompt_target_segment_duration": 3.3,
        },
        hook_templates=[
            "笑死我了{movie}这段真的绝了",
            "{movie}这个梗我能笑一年",
            "别在吃饭时看{movie}真的会喷",
        ],
        style_hint="只讲最搞笑的三个名场面",
        rationale="喜剧需要铺垫包袱的节奏，稍长的 hook 时间让笑点有蓄力空间。",
    ),
    "悬疑": GenreProfile(
        preset="mainstream-dry",
        params={
            "match_speed_clamp_min": 0.80,
            "match_speed_clamp_max": 1.20,
            "match_timeline_mode": "weighted_acts",
            "match_act_weights": [0.10, 0.25, 0.45, 0.20],
            "bgm_duck_db": -14.0,
            "prompt_target_sentences": 12,
            "prompt_target_segment_duration": 5.0,
            "render_title_card_sec": 0.8,
        },
        hook_templates=[
            "这部悬疑片的结局我没想到{movie}",
            "{movie}的反转你看出来了吗",
            "看到最后一秒我才恍然大悟{movie}",
        ],
        style_hint="只讲最后的反转，前面全是铺垫",
        rationale="悬疑片高光集中在后段，用 weighted_acts 加大高潮幕权重。慢节奏留悬念。",
    ),
    "恐怖": GenreProfile(
        preset="mainstream-dry",
        params={
            "match_speed_clamp_min": 0.80,
            "match_speed_clamp_max": 1.15,
            "bgm_duck_db": -14.0,
            "tts_pause_ms": 300,
            "prompt_target_sentences": 12,
            "prompt_target_segment_duration": 5.0,
        },
        hook_templates=[
            "这部恐怖片吓得我三天没睡好{movie}",
            "{movie}这段我全程捂着眼看的",
            "深夜千万别一个人看{movie}",
        ],
        style_hint="聚焦最恐怖的三个场景",
        rationale="恐怖片需要留白和停顿制造紧张感。增加句间停顿，收紧速度上限避免画面过快削弱恐惧。",
    ),
    "爱情": GenreProfile(
        preset="bilibili-long",
        params={
            "match_speed_clamp_min": 0.85,
            "match_speed_clamp_max": 1.15,
            "bgm_duck_db": -16.0,
            "prompt_target_sentences": 8,
            "prompt_target_segment_duration": 7.5,
            "render_title_card_sec": 1.2,
        },
        hook_templates=[
            "看完{movie}我又相信爱情了",
            "{movie}这段告白我看哭了",
            "这才是爱情片的天花板{movie}",
        ],
        style_hint="聚焦感情线的高潮时刻",
        rationale="爱情片需要情感铺垫，长解说慢节奏让情绪有呼吸空间。",
    ),
    "文艺": GenreProfile(
        preset="bilibili-long",
        params={
            "match_speed_clamp_min": 0.85,
            "match_speed_clamp_max": 1.10,
            "bgm_duck_db": -18.0,
            "prompt_target_sentences": 8,
            "prompt_target_segment_duration": 8.0,
            "render_title_card_sec": 1.2,
        },
        hook_templates=[
            "这部文艺片后劲太大了{movie}",
            "{movie}讲透了人生的一个真相",
            "静下心来看完{movie}你会发现不一样的东西",
        ],
        style_hint="聚焦人物内心变化的一个转折点",
        rationale="文艺片节奏慢，长段叙事。收紧速度上限保持画面原味，极轻 BGM 不干扰氛围。",
    ),
    "纪录片": GenreProfile(
        preset="bilibili-long",
        params={
            "match_speed_clamp_min": 0.90,
            "match_speed_clamp_max": 1.15,
            "bgm_duck_db": -18.0,
            "prompt_target_sentences": 8,
            "prompt_target_segment_duration": 7.5,
        },
        hook_templates=[
            "这部纪录片颠覆了我的认知{movie}",
            "{movie}告诉你一个你不知道的真相",
            "看完{movie}我沉默了很久",
        ],
        style_hint="提炼最震撼的一个信息点",
        rationale="纪录片信息密度均匀，长解说适合传递信息。中等速度保持画面可读性。",
    ),
    "动画": GenreProfile(
        preset="douyin-fast",
        params={
            "match_speed_clamp_min": 0.90,
            "match_speed_clamp_max": 1.25,
            "bgm_duck_db": -10.0,
            "prompt_target_sentences": 18,
            "prompt_target_segment_duration": 3.3,
        },
        hook_templates=[
            "这部动画的作画太牛了{movie}",
            "{movie}这段我逐帧暂停看的",
            "动画能做到这种程度{movie}",
        ],
        style_hint="聚焦作画最炸裂的名场面",
        rationale="动画画面信息密度大，不宜过度慢放。快切节奏适合展示作画高光。",
    ),
}


def generate_yaml(profile: GenreProfile, genre: str, duration: int, fmt: str) -> str:
    """Generate YAML config snippet."""
    lines = [
        f"# 片种分流配置 — {genre}片",
        f"# 生成自 genre_advisor.py",
        f"# 理由: {profile.rationale}",
        f"narration_preset: {profile.preset}",
        f"",
        f"params:",
    ]

    for key, val in profile.params.items():
        if isinstance(val, float):
            lines.append(f"  {key}: {val}")
        elif isinstance(val, list):
            lines.append(f"  {key}: {val}")
        else:
            lines.append(f"  {key}: {val}")

    if profile.hook_templates:
        lines.append(f"  hook_templates:")
        for tmpl in profile.hook_templates:
            lines.append(f'    - "{tmpl}"')

    lines.append(f"")
    lines.append(f"# 推荐 --style: \"{profile.style_hint}\"")

    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="片种分流顾问 — 按电影类型推荐 preset 和参数")
    parser.add_argument("--genre", required=True, help="电影类型（动作/科幻/喜剧/悬疑/恐怖/爱情/文艺/纪录片/动画）")
    parser.add_argument("--duration", type=int, default=60, help="目标成片时长（秒）")
    parser.add_argument("--format", default="16:9", choices=["16:9", "9:16"], help="视频比例")
    parser.add_argument("--yaml", action="store_true", help="输出 YAML 配置片段")
    args = parser.parse_args()

    genre = args.genre.strip()

    # Fuzzy match
    matched_genre = None
    for key in GENRE_PROFILES:
        if key in genre or genre in key:
            matched_genre = key
            break

    if not matched_genre:
        print(f"未找到片种 '{genre}' 的配置")
        print(f"支持的片种: {', '.join(GENRE_PROFILES.keys())}")
        sys.exit(1)

    profile = GENRE_PROFILES[matched_genre]

    if args.yaml:
        print(generate_yaml(profile, matched_genre, args.duration, args.format))
    else:
        print("=" * 60)
        print(f"片种分流建议 — {matched_genre}片")
        print("=" * 60)
        print()
        print(f"推荐 Preset: {profile.preset}")
        print(f"推荐 Style:  {profile.style_hint}")
        print()
        print(f"理由: {profile.rationale}")
        print()
        print("推荐参数:")
        print("-" * 40)
        for key, val in profile.params.items():
            print(f"  {key:<35} = {val}")
        print()
        print("推荐钩子模板:")
        print("-" * 40)
        for tmpl in profile.hook_templates:
            print(f"  - {tmpl}")
        print()
        print("YAML 配置片段:")
        print("-" * 40)
        print(generate_yaml(profile, matched_genre, args.duration, args.format))
        print()
        print(f"使用方法: 将以上 YAML 保存为 job.{matched_genre}.yaml，然后:")
        print(f"  mn create --movie '片名' --style '{profile.style_hint}' \\")
        print(f"    --duration {args.duration} --format {args.format} \\")
        print(f"    --config job.{matched_genre}.yaml \\")
        print(f"    --video /path/to/video.mp4 --bgm /path/to/bgm.mp3")


if __name__ == "__main__":
    main()
