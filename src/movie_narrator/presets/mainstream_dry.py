# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Mainstream dry commentary preset — 12 sentences × 5s, slow cuts, thick backplate subtitles.

Mainstream dry editing style (Gu Amo / Movie Hurricane rhythm). Slower sentence pace, steady cuts, light BGM.
Suitable for "movie quick recap" medium-long videos.
"""

from typing import Any, Dict


class MainstreamDryPreset:
    """Mainstream dry style — Gu Amo / Movie Hurricane rhythm."""

    name = "mainstream-dry"

    def render_template(self) -> Dict[str, Any]:
        """Landscape long-form video wrapper template — clean title, no watermark, no disclaimer."""
        return {
            "title_card_text": "{movie}",
            "end_card_text": "感谢观看",
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
            # Prompt: 12 句×~5s (60s 基准), max_chars 按字速 3.8 字/s 计算
            # 5.0s × 3.8 = 19.0 字, max_chars=22 留 16% 余量
            "prompt_target_sentences": 12,
            "prompt_target_segment_duration": 5.0,
            "prompt_max_chars_per_sentence": 22,
            "prompt_hook_seconds": 5,
            # Hook templates — measured, curiosity-driven openings
            "hook_templates": [
                "{movie}是一部被低估的佳作",
                "关于{movie}，有个细节你可能没注意",
                "{movie}的故事，远比表面看到的复杂",
            ],
            # Platform tone adaptation
            "target_platform": "youtube",
            # Render template — per-preset styling overlays
            "render_template": self.render_template(),
        }

    def prompt_tags(self) -> Dict[str, str]:
        """Return the prompt style tags for this preset.

        Returns:
            Dictionary of prompt style tags.
        """
        return {
            "prompt_cadence": "measured",
            "prompt_register": "spoken",
            "prompt_connectors": "narrative",
        }

    def description(self) -> str:
        """Return a human-readable description of the preset.

        Returns:
            Preset description string.
        """
        return "主流干货 — 12句×5s, 慢切镜, 谷阿莫/影视飓风节奏"
