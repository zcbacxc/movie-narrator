# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared constants and helpers for the clip-matching package."""

from typing import List

from ...models import Context

_EMBEDDING_MODEL_NAME = (
    "paraphrase-multilingual-MiniLM-L12-v2"  # default, overridden by ctx.metadata
)


def _resolve_match_texts(ctx: Context) -> List[str]:
    """
    Returns:
        The narration texts to embed for matching in the target language.

        i18n pipeline (v0.9.6): when ``translated_texts`` are present and aligned
        with ``timed_segments`` (i.e. the narration was translated to
        ``subtitle_lang``), the embedding re-rank should match against the
        target-language text so it aligns with the scene captions' transcription
        language. Otherwise fall back to the narration segments' own text (which is
        already written in the narration ``lang``).
    """
    if ctx.translated_texts and len(ctx.translated_texts) == len(ctx.timed_segments):
        return list(ctx.translated_texts)
    return [seg.text for seg in ctx.timed_segments]
