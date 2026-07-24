"""Bilibili-long preset — 8 句×7.5s, 大场景合并, 字幕小.

B站长解说风格。慢节奏,突出源片,相邻 scene 合并,字幕更小更克制。
适合粉丝留存型长解说。
"""

from typing import Any, Dict


class BilibiliLongPreset:
    """B站长解说风格 — 慢节奏, 突出源片。"""

    name = "bilibili-long"

    def params(self) -> Dict[str, Any]:
        return {
            # Match: 大场景合并,几乎不拉伸
            "match_speed_clamp_min": 0.95,
            "match_speed_clamp_max": 1.02,
            "scene_merge_min_duration": 5.0,
            "match_drop_scene_min_duration": 0.8,
            # BGM: 很轻
            "bgm_duck_db": -18.0,
            "bgm_normalize": True,
            "audio_target_dbfs": -16.0,
            # Render: 小字幕,克制
            "render_subtitle_position": "bottom",
            "render_font_size": 75,
            # TTS: 长停顿,留白
            "tts_pause_ms": 300,
            # Prompt: 8 句×~7.5s (60s 基准), max_chars 按字速 3.8 字/s 计算
            # 7.5s × 3.8 = 28.5 字, max_chars=32 留 12% 余量
            "prompt_target_sentences": 8,
            "prompt_target_segment_duration": 7.5,
            "prompt_max_chars_per_sentence": 32,
            "prompt_hook_seconds": 7,
            # EP4: hook templates — analytical, depth-driven openings
            "hook_templates": [
                "今天聊聊{movie}，一部被时间证明的经典",
                "{movie}为什么值得反复观看？",
                "从{movie}看导演的叙事野心",
            ],
            # EP5: title card for long-form
            "render_title_card_sec": 1.2,
        }

    def prompt_tags(self) -> Dict[str, str]:
        return {
            "prompt_cadence": "languid",
            "prompt_register": "written",
            "prompt_connectors": "narrative",
        }

    def description(self) -> str:
        return "B站长解说 — 8句×7.5s, 慢节奏, 突出源片, 粉丝留存型"
