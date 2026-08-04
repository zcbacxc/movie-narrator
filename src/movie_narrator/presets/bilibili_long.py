# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Bilibili long-form preset — 8 sentences × 7.5s, large scene merge, small subtitles.

Bilibili long commentary style. Slow pace, emphasizes source footage, merges adjacent scenes, smaller and more restrained subtitles.
Suitable for fan-retention long-form commentary.
"""

from typing import Any, Dict


class BilibiliLongPreset:
    """Bilibili long commentary style — slow pace, emphasizes source footage."""

    name = "bilibili-long"

    def render_template(self) -> Dict[str, Any]:
        """Landscape long-form video wrapper template — with end card and disclaimer."""
        return {
            "title_card_text": "{movie}",
            "disclaimer_text": "解说仅供交流，请支持正版",
            "end_card_text": "一键三连",
            "aspect_safe_area": {
                "max_width_ratio": 0.90,
                "bottom_margin_ratio": 0.08,
            },
        }

    def params(self) -> Dict[str, Any]:
        """Return the preset parameter dictionary.

        Returns:
            Dictionary of preset parameters.
        """
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
            # Hook templates — analytical, depth-driven openings
            "hook_templates": [
                "今天聊聊{movie}，一部被时间证明的经典",
                "{movie}为什么值得反复观看？",
                "从{movie}看导演的叙事野心",
            ],
            # Title card, cover export, and vertical safe area for long-form
            "render_title_card_sec": 1.2,
            "render_cover_export": True,
            "render_vertical_safe_area": True,
            # Platform tone adaptation
            "target_platform": "bilibili",
            # Render template — per-preset styling overlays
            "render_template": self.render_template(),
        }

    def prompt_tags(self) -> Dict[str, str]:
        """Return the prompt style tags for this preset.

        Returns:
            Dictionary of prompt style tags.
        """
        return {
            "prompt_cadence": "languid",
            "prompt_register": "written",
            "prompt_connectors": "narrative",
        }

    def description(self) -> str:
        """Return a human-readable description of the preset.

        Returns:
            Preset description string.
        """
        return "B站长解说 — 8句×7.5s, 慢节奏, 突出源片, 粉丝留存型"
