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
            # Render: 紧凑字幕
            "render_subtitle_position": "bottom",
            "render_font_size": 100,
            # TTS: 紧凑停顿
            "tts_pause_ms": 150,
            # Prompt: 18 句×~3.3s
            "prompt_target_sentences": 18,
            "prompt_max_chars_per_sentence": 15,
            "prompt_hook_seconds": 3,
        }

    def prompt_tags(self) -> Dict[str, str]:
        return {
            "prompt_cadence": "brisk",
            "prompt_register": "spoken",
            "prompt_connectors": "interjection",
        }

    def description(self) -> str:
        return "抖音快剪 — 18句×3.3s, 快切镜, 高完播率短视频风格"
