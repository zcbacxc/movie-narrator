# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ASR (automatic speech recognition) providers.

G6: hosts optional transcription backends for the align step. Unlike the
plugin registries (TTS / Vision / LLM / Research), these are selected by
:func:`movie_narrator.pipeline._align_backend.select_align_backend` and
return a uniform ``wx_segments`` list rather than a registered provider.
"""
