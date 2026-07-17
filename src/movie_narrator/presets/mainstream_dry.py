"""Mainstream-dry preset — 12 句×5s, 慢切镜, 厚背板字幕.

主流干货剪辑风格(谷阿莫/影视飓风节奏)。句速放慢,切镜稳,BGM 轻。
适合"电影速看"类中长视频。
"""

from typing import Any, Dict


class MainstreamDryPreset:
    """主流干货风格 — 谷阿莫/影视飓风节奏。"""

    name = "mainstream-dry"

    def params(self) -> Dict[str, Any]:
        return {
            # Match: 慢切镜,拒绝大幅拉伸
            "match_speed_clamp_min": 0.9,
            "match_speed_clamp_max": 1.05,
            "scene_merge_min_duration": 3.5,
            "match_drop_scene_min_duration": 0.5,
            # BGM: 更轻,不抢人声
            "bgm_duck_db": -15.0,
            "bgm_normalize": True,
            "audio_target_dbfs": -14.0,
            # Render: 厚背板字幕
            "render_subtitle_position": "bottom",
            "render_font_size": 90,
            # TTS: 留呼吸
            "tts_pause_ms": 200,
            # Prompt: 12 句×~5s
            "prompt_target_sentences": 12,
            "prompt_max_chars_per_sentence": 18,
            "prompt_hook_seconds": 5,
        }

    def prompt_tags(self) -> Dict[str, str]:
        return {
            "prompt_cadence": "measured",
            "prompt_register": "spoken",
            "prompt_connectors": "narrative",
        }

    def description(self) -> str:
        return "主流干货 — 12句×5s, 慢切镜, 谷阿莫/影视飓风节奏"
