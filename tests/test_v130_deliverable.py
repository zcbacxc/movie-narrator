# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.3.0 Feature 3 — versioned deliverable manifest.

Covers:
- ``write_deliverable_manifest``: correct SHA-256 checksums, clips
  inclusion, missing core artifacts → ``present=false``, optional kinds
  only when present, atomic write (no leftover temp file).
- Manifest header fields (schema/package/contract versions, movie,
  generation mode, generated_at UTC).
- Runner integration: a fake-step ``run_pipeline`` run produces the
  manifest and records its path in ``ctx.metadata["deliverable_manifest"]``;
  dry-run skips.
- Contract exports are importable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import movie_narrator.pipeline.runner as runner_mod
from movie_narrator import __version__
from movie_narrator.contract import CONTRACT_VERSION
from movie_narrator.models import Context, Services, SubtitlePaths
from movie_narrator.pipeline.deliverable import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    build_deliverable_manifest,
    write_deliverable_manifest,
)
from movie_narrator.pipeline.runner import STEPS, run_pipeline


# ── Helpers ────────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> Context:
    return Context(
        movie_name="test-movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )


def _write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ── Manifest content ───────────────────────────────────────


class TestManifestContent:
    def test_checksums_correct(self, tmp_path: Path):
        """sha256 values match independently computed checksums."""
        video = _write(tmp_path / "final.mp4", b"fake video bytes")
        ctx = _make_ctx(tmp_path)
        ctx.video_path = str(video)
        write_deliverable_manifest(ctx)
        data = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        video_entry = next(a for a in data["artifacts"] if a["kind"] == "video")
        assert video_entry["present"] is True
        assert video_entry["bytes"] == len(b"fake video bytes")
        assert video_entry["sha256"] == hashlib.sha256(b"fake video bytes").hexdigest()
        assert video_entry["path"] == "final.mp4"

    def test_clips_included(self, tmp_path: Path):
        """clips/*.mp4 files become clip entries with checksums."""
        _write(tmp_path / "clips" / "scene_0001.mp4", b"clip1")
        _write(tmp_path / "clips" / "scene_0002.mp4", b"clip2")
        ctx = _make_ctx(tmp_path)
        ctx.clips_dir = str(tmp_path / "clips")
        write_deliverable_manifest(ctx)
        data = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        clips = [a for a in data["artifacts"] if a["kind"] == "clip"]
        assert [c["path"] for c in clips] == [
            "clips/scene_0001.mp4",
            "clips/scene_0002.mp4",
        ]
        assert all(c["present"] for c in clips)
        assert clips[0]["sha256"] == hashlib.sha256(b"clip1").hexdigest()

    def test_missing_core_artifacts_present_false(self, tmp_path: Path):
        """Video/audio/subtitle entries always appear; missing → present=false."""
        ctx = _make_ctx(tmp_path)  # no artifacts at all
        write_deliverable_manifest(ctx)
        data = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        kinds = {a["kind"] for a in data["artifacts"]}
        assert {"video", "audio", "subtitle"} <= kinds
        core = [a for a in data["artifacts"] if a["kind"] in {"video", "audio", "subtitle"}]
        assert all(a["present"] is False for a in core)
        assert all(a["bytes"] == 0 and a["sha256"] == "" for a in core)
        # Conventional declared paths keep the manifest shape stable.
        video = next(a for a in data["artifacts"] if a["kind"] == "video")
        assert video["path"] == "final.mp4"

    def test_optional_kinds_only_when_present(self, tmp_path: Path):
        """script/metadata/execution_manifest kinds appear only if the
        files exist."""
        ctx = _make_ctx(tmp_path)
        write_deliverable_manifest(ctx)
        data = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        kinds = {a["kind"] for a in data["artifacts"]}
        assert "script" not in kinds
        assert "metadata" not in kinds
        assert "execution_manifest" not in kinds
        assert "clip" not in kinds

    def test_subtitle_variants_included(self, tmp_path: Path):
        """All subtitle variants carried by ctx.subtitle_paths are indexed."""
        original = _write(tmp_path / "subtitle.srt", b"orig")
        translated = _write(tmp_path / "subtitle.en.srt", b"trans")
        bilingual = _write(tmp_path / "subtitle.bilingual.srt", b"bi")
        ctx = _make_ctx(tmp_path)
        ctx.subtitle_paths = SubtitlePaths(
            original=str(original),
            translated=str(translated),
            bilingual=str(bilingual),
        )
        manifest = build_deliverable_manifest(ctx)
        subs = [a for a in manifest.artifacts if a.kind == "subtitle"]
        assert len(subs) == 3
        assert all(s.present for s in subs)

    def test_header_fields(self, tmp_path: Path):
        """schema/package/contract versions, movie, mode, UTC timestamp."""
        ctx = _make_ctx(tmp_path)
        manifest = build_deliverable_manifest(ctx)
        assert manifest.schema_version == MANIFEST_SCHEMA_VERSION == 1
        assert manifest.package_version == __version__
        assert manifest.contract_version == ".".join(str(v) for v in CONTRACT_VERSION)
        assert manifest.movie == "test-movie"
        assert manifest.generation_mode == "full"
        ts = datetime.fromisoformat(manifest.generated_at.replace("Z", "+00:00"))
        assert ts.utcoffset() == timedelta(0)

    def test_preview_mode_generation(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        ctx.metadata["render_preview_mode"] = True
        assert build_deliverable_manifest(ctx).generation_mode == "preview"

    def test_qa_block_collected(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        ctx.metadata["qa_report"] = {"overall_score": 0.9}
        ctx.metadata["video_qa"] = {"codec": "h264"}
        manifest = build_deliverable_manifest(ctx)
        assert manifest.qa == {"qa_report": {"overall_score": 0.9}, "video_qa": {"codec": "h264"}}


# ── Atomic write ───────────────────────────────────────────


class TestAtomicWrite:
    def test_no_tmp_file_left_behind(self, tmp_path: Path):
        """os.replace leaves no .tmp file next to the manifest."""
        ctx = _make_ctx(tmp_path)
        path = write_deliverable_manifest(ctx)
        assert path == tmp_path / MANIFEST_FILENAME
        assert path.is_file()
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_overwrite_existing_manifest(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        first = write_deliverable_manifest(ctx)
        ctx.video_path = str(_write(tmp_path / "final.mp4", b"v"))
        second = write_deliverable_manifest(ctx)
        assert first == second
        data = json.loads(second.read_text(encoding="utf-8"))
        video = next(a for a in data["artifacts"] if a["kind"] == "video")
        assert video["present"] is True


# ── Runner integration ─────────────────────────────────────


class TestRunnerIntegration:
    def _fake_pipeline(self, monkeypatch, video_bytes: bytes, tmp_path: Path):
        """Fake steps: render sets video_path, QA sets metadata."""
        patched = []
        for step in list(STEPS):
            name = step.__name__

            def make_mock(n):
                def mock_step(c):
                    if n == "render_video":
                        video = tmp_path / "final.mp4"
                        video.write_bytes(video_bytes)
                        c.video_path = str(video)
                    if n == "validate_deliverable":
                        c.metadata["qa_report"] = {"overall_score": 0.9}
                    return c

                mock_step.__name__ = n
                return mock_step

            patched.append(make_mock(name))
        monkeypatch.setattr(runner_mod, "STEPS", patched)
        monkeypatch.setattr(runner_mod, "run_preflight", lambda ctx: None)

    def test_run_pipeline_produces_manifest(self, tmp_path: Path, monkeypatch):
        """A completed pipeline writes the manifest and records its path."""
        ctx = _make_ctx(tmp_path)
        self._fake_pipeline(monkeypatch, b"final video", tmp_path)

        run_pipeline(ctx)

        manifest_path = tmp_path / MANIFEST_FILENAME
        assert manifest_path.is_file()
        assert ctx.metadata["deliverable_manifest"] == str(manifest_path)
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        video = next(a for a in data["artifacts"] if a["kind"] == "video")
        assert video["present"] is True
        assert video["sha256"] == hashlib.sha256(b"final video").hexdigest()
        assert data["qa"]["qa_report"] == {"overall_score": 0.9}

    def test_dry_run_skips_manifest(self, tmp_path: Path, monkeypatch):
        """Dry-run has no media — the manifest is skipped entirely."""
        ctx = _make_ctx(tmp_path)
        ctx.metadata["dry_run"] = True
        self._fake_pipeline(monkeypatch, b"final video", tmp_path)

        run_pipeline(ctx)

        assert not (tmp_path / MANIFEST_FILENAME).exists()
        assert "deliverable_manifest" not in ctx.metadata

    def test_no_video_skips_manifest(self, tmp_path: Path, monkeypatch):
        """Runs without a rendered video keep the previous file-set behavior."""
        ctx = _make_ctx(tmp_path)
        patched = []
        for step in list(STEPS):

            def make_mock(n):
                def mock_step(c):
                    return c

                mock_step.__name__ = n
                return mock_step

            patched.append(make_mock(step.__name__))
        monkeypatch.setattr(runner_mod, "STEPS", patched)
        monkeypatch.setattr(runner_mod, "run_preflight", lambda ctx: None)

        run_pipeline(ctx)

        assert not (tmp_path / MANIFEST_FILENAME).exists()


# ── Contract exports ───────────────────────────────────────


class TestContractExports:
    def test_importable_from_contract(self):
        from movie_narrator.contract import (
            DeliverableManifest,
            ManifestEntry,
            write_deliverable_manifest as w,
        )

        assert DeliverableManifest is not None
        assert ManifestEntry is not None
        assert callable(w)

    @pytest.mark.parametrize(
        "name", ["DeliverableManifest", "ManifestEntry", "write_deliverable_manifest"]
    )
    def test_in_all(self, name):
        from movie_narrator import contract

        assert name in contract.__all__
