"""Douyin-fast preset — 18 句×3.3s, 快切镜, 紧凑.

60s 短视频高完播率风格。句密高,切镜快,BGM 闪避深。
这是 v0.4.13 的行为基线,作为默认 preset 保证向后兼容。
"""

from typing import Any, Dict


class DouyinFastPreset:
    """抖音快剪风格 — 高完播率短视频。"""

    name = "douyin-fast"

    def params(self) -> Dict[str, Any]:
        return {
            # Match: 快切镜,允许较大速度拉伸
            "match_speed_clamp_min": 0.85,
            "match_speed_clamp_max": 1.25,
            "scene_merge_min_duration": 2.0,
            "match_drop_scene_min_duration": 0.4,
            # BGM: 深度闪避,不抢人声
            "bgm_duck_db": -10.0,
            "bgm_normalize": True,
            "audio_target_dbfs": -14.0,
            # EP6: loudnorm for consistent loudness across short-form content
            "bgm_loudnorm": True,
            # Render: 紧凑字幕
            "render_subtitle_position": "bottom",
            "render_font_size": 100,
            # TTS: 紧凑停顿
            "tts_pause_ms": 150,
            # Prompt: 18 句×~3.3s (60s 基准), max_chars 按字速 3.8 字/s 计算
            "prompt_target_sentences": 18,
            "prompt_target_segment_duration": 3.3,
            "prompt_max_chars_per_sentence": 15,
            "prompt_hook_seconds": 3,
            # EP4: hook templates — punchy, scroll-stop openings
            "hook_templates": [
                "你敢信？{movie}里这段直接封神",
                "看完{movie}我三天没缓过来",
                "{movie}最炸裂的一幕，不看后悔",
                "别被{movie}的片名骗了，这片太猛了",
                "{movie}里这个反转，我看了五遍才懂",
            ],
            # EP5: title card
            "render_title_card_sec": 1.0,
        }

    def prompt_tags(self) -> Dict[str, str]:
        return {
            "prompt_cadence": "brisk",
            "prompt_register": "spoken",
            "prompt_connectors": "interjection",
        }

    def description(self) -> str:
        return "抖音快剪 — 18句×3.3s, 快切镜, 高完播率短视频风格"
