# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard tests for the v1.4.1 output-format stability promise (Feature 2).

STABILITY.md historically excluded output file formats from the API
stability promise. v1.3.0 shipped ``deliverable_manifest.json`` (versioned
schema + per-artifact SHA-256 checksums) — the precondition the ROADMAP
named for making a versioned compatibility commitment. v1.4.1 adds a
narrow "Output Format Compatibility" section to STABILITY.md (EN + ZH).

These tests are cheap doc-drift tripwires:

- the section must exist in both languages and mention
  ``deliverable_manifest.json`` + ``schema_version``;
- ``pipeline/deliverable.MANIFEST_SCHEMA_VERSION`` must still be 1 —
  if you bump it, update the STABILITY docs (breaking shape changes
  bump ``schema_version`` by contract).
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_EN_HEADING = "## Output Format Compatibility (v1.4.1+)"
_ZH_HEADING = "## 输出格式兼容性 (v1.4.1+)"


def _section(text: str, heading: str) -> str:
    assert heading in text, f"missing heading in STABILITY doc: {heading!r}"
    match = re.search(re.escape(heading) + r".*?(?=\n## |\Z)", text, re.S)
    assert match is not None, f"section body not found for {heading!r}"
    return match.group(0)


class TestStabilityOutputFormatSection:
    def test_en_section_exists_and_covers_promise(self):
        text = (REPO_ROOT / "docs" / "STABILITY.md").read_text(encoding="utf-8")
        section = _section(text, _EN_HEADING)
        # The promise is anchored on the deliverable manifest + versioning.
        assert "deliverable_manifest.json" in section
        assert "schema_version" in section
        assert "MANIFEST_SCHEMA_VERSION" in section
        # Default deliverable set is named explicitly.
        assert "final.mp4" in section
        assert "narration.mp3" in section
        assert "subtitle.srt" in section
        # muxed subtitles are documented as provided-as-is.
        assert "mov_text" in section

    def test_zh_section_mirrors_en(self):
        text = (REPO_ROOT / "docs" / "STABILITY.zh-CN.md").read_text(encoding="utf-8")
        section = _section(text, _ZH_HEADING)
        assert "deliverable_manifest.json" in section
        assert "schema_version" in section
        assert "MANIFEST_SCHEMA_VERSION" in section
        assert "final.mp4" in section
        assert "narration.mp3" in section
        assert "subtitle.srt" in section
        assert "mov_text" in section

    def test_not_covered_bullet_carved_out_consistently(self):
        """The old blanket 'output file formats NOT covered' bullet must
        now carve out the narrow promise instead of contradicting it."""
        for name in ("STABILITY.md", "STABILITY.zh-CN.md"):
            text = (REPO_ROOT / "docs" / name).read_text(encoding="utf-8")
            assert "Output file formats (video encoding, subtitle styling)" not in text
            assert "输出文件格式（视频编码、字幕样式）" not in text

    def test_manifest_schema_version_matches_promise(self):
        """schema_version 1 is part of the documented promise. If you bump
        MANIFEST_SCHEMA_VERSION, update the STABILITY docs (both languages)
        in the same release — this test is the reminder."""
        from movie_narrator.pipeline.deliverable import MANIFEST_SCHEMA_VERSION

        assert MANIFEST_SCHEMA_VERSION == 1
