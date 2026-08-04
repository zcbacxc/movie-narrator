# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Douyin fast-paced preset — 18 sentences × 3.3s, fast cuts, tight pacing.

60s short-form high-completion-rate style. Dense sentences, fast cuts, deep BGM ducking.
This is the v0.4.13 behavior baseline, used as the default preset for backward compatibility.
"""

from typing import Any, Dict


class DouyinFastPreset:
    """Douyin fast-cut style — high completion rate short-form videos."""

    name = "douyin-fast"

    def render_template(self) -> Dict[str, Any]:
        """Portrait short-form video wrapper template — with watermark and disclaimer."""
        return {
            "title_card_text": "{movie}",
            "watermark_text": "{movie}解说",
            "disclaimer_text": "本视频仅供娱乐交流，如有侵权请联系删除",
            "slogan_text": "关注不迷路",
            "end_card_text": "点赞+关注",
            "aspect_safe_area": {
                "max_width_ratio": 0.82,
                "bottom_margin_ratio": 0.15,
            },
        }

    def params(self) -> Dict[str, Any]:
        """Return the preset parameter dictionary.

        Returns:
            Dictionary of preset parameters.
        """
        return {
            # Match: fast cuts, allow larger speed stretch
            "match_speed_clamp_min": 0.85,
            "match_speed_clamp_max": 1.25,
            "scene_merge_min_duration": 2.0,
            "match_drop_scene_min_duration": 0.4,
            # BGM: deep ducking, don't overpower the voice
            "bgm_duck_db": -10.0,
            "bgm_normalize": True,
            "audio_target_dbfs": -14.0,
            # RMS-based loudnorm for consistent loudness across short-form content
            "bgm_loudnorm": True,
            # Render: compact subtitles
            "render_subtitle_position": "bottom",
            "render_font_size": 100,
            # TTS: tight pauses
            "tts_pause_ms": 150,
            # Prompt: 18 sentences x ~3.3s (60s baseline); max_chars at 3.8 chars/s
            "prompt_target_sentences": 18,
            "prompt_target_segment_duration": 3.3,
            "prompt_max_chars_per_sentence": 15,
            "prompt_hook_seconds": 3,
            # Hook templates — punchy, scroll-stop openings
            "hook_templates": [
                "你敢信？{movie}里这段直接封神",
                "看完{movie}我三天没缓过来",
                "{movie}最炸裂的一幕，不看后悔",
                "别被{movie}的片名骗了，这片太猛了",
                "{movie}里这个反转，我看了五遍才懂",
            ],
            # Title card, cover export, and vertical safe area
            "render_title_card_sec": 1.0,
            "render_cover_export": True,
            "render_vertical_safe_area": True,
            # Platform tone adaptation
            "target_platform": "douyin",
            # Render template — per-preset styling overlays
            "render_template": self.render_template(),
        }

    def prompt_tags(self) -> Dict[str, str]:
        """Return the prompt style tags for this preset.

        Returns:
            Dictionary of prompt style tags.
        """
        return {
            "prompt_cadence": "brisk",
            "prompt_register": "spoken",
            "prompt_connectors": "interjection",
        }

    def description(self) -> str:
        """Return a human-readable description of the preset.

        Returns:
            Preset description string.
        """
        return "抖音快剪 — 18句×3.3s, 快切镜, 高完播率短视频风格"
