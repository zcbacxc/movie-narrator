# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""StubVisionCaptioner — placeholder captions for CI and dev (vision captioner integration).

Returns:
    Deterministic placeholder labels so the embedding re-rank
    path stays exercisable without a real vision model. Directly
    analogous to BaseTTSProvider._silent_synthesize for CI.
"""

from typing import List, Optional

from ..models import Scene
from .protocol import VisionCaptioner


class StubVisionCaptioner(VisionCaptioner):
    """
    Returns:
        Placeholder scene labels without any ML model.

        Labels follow the same format as match._build_scene_label:
        ``"scene {index} from {start}s to {end}s"``. This ensures
        backward compatibility — the fake-caption guard in match.py
        will detect these as placeholders and fall back to heuristic
        matching, exactly as before vision captioner integration.
    """

    def caption_scenes(
        self,
        scenes: List[Scene],
        video_path: Optional[str] = None,
    ) -> List[str]:
        """Generate captions for video scenes."""
        return [f"scene {s.index} from {s.start:.1f}s to {s.end:.1f}s" for s in scenes]
