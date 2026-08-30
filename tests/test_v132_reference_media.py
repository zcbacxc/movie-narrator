# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the v1.3.2 reference-media input contract (Feature 8).

Covers: schema model + coercion, load whitelist + relative path
resolution, merge inclusion, resolve-step validation (existence +
kind/extension), prompt augmentation with a fake LLM, image captioning
via a fake vision provider (incl. soft-degrade), and the empty-default
byte-identical guarantee.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from movie_narrator.models import Services
from movie_narrator.pipeline.resolve import resolve_video
from movie_narrator.pipeline.script import (
    _build_reference_media_hints,
    _generate_plot_beats,
    _maybe_caption_reference_images,
    generate_script,
)
from movie_narrator.providers import register_vision
from movie_narrator.vision.protocol import VisionCaptioner
from movie_narrator.workflow.load import load_job_config
from movie_narrator.workflow.merge import merge_job
from movie_narrator.workflow.schema import JobParams, ReferenceMediaItem
from movie_narrator.config import Settings


# ── Helpers ───────────────────────────────────────────────


def _make_ctx(tmp_path, **kw):
    defaults = dict(
        movie_name="test_movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )
    defaults.update(kw)
    from movie_narrator.models import Context

    return Context(**defaults)


def _mock_llm_response(json_str: str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json_str
    return resp


def _mock_llm_cm(response=None, side_effect=None):
    mock_llm = MagicMock()
    mock_llm.model = "test-model"
    if side_effect:
        mock_llm.client.chat.completions.create.side_effect = side_effect
    else:
        mock_llm.client.chat.completions.create.return_value = response
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_llm)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


def _mock_settings(**overrides):
    s = MagicMock()
    s.script_retries = overrides.get("script_retries", 3)
    s.script_retry_delay = overrides.get("script_retry_delay", 0)
    s.script_max_tokens = overrides.get("script_max_tokens", 2048)
    s.research_temperature = overrides.get("research_temperature", 0.3)
    s.research_max_tokens = overrides.get("research_max_tokens", 1024)
    return s


def _beats_json(n: int) -> str:
    beats = [f"剧情关键点{i + 1}" for i in range(n)]
    return '{"beats": ' + str(beats).replace("'", '"') + "}"


def _segments_json(texts: list) -> str:
    return json.dumps({"segments": [{"text": t} for t in texts]}, ensure_ascii=False)


def _write_ref_video(tmp_path: Path, name: str = "ref.mp4") -> str:
    p = tmp_path / name
    p.write_bytes(b"fake video bytes")
    return str(p)


# ── 1. Schema ─────────────────────────────────────────────


class TestReferenceMediaSchema:
    def test_item_defaults(self):
        item = ReferenceMediaItem(path="a.mp4")
        assert item.kind == "video"
        assert item.usage == "style"
        assert item.note == ""

    def test_params_coerces_list_of_dicts_to_tuple(self):
        params = JobParams.model_validate(
            {"reference_media": [{"path": "a.mp4", "kind": "video", "usage": "pacing"}]}
        )
        assert isinstance(params.reference_media, tuple)
        assert len(params.reference_media) == 1
        assert isinstance(params.reference_media[0], ReferenceMediaItem)
        assert params.reference_media[0].usage == "pacing"

    def test_params_default_empty_tuple(self):
        params = JobParams()
        assert params.reference_media == ()

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValidationError):
            ReferenceMediaItem(path="a.mp4", kind="audio")

    def test_invalid_usage_rejected(self):
        with pytest.raises(ValidationError):
            ReferenceMediaItem(path="a.mp4", usage="vibe")


# ── 2. Load whitelist + relative path resolution ──────────


class TestLoad:
    def _write_yaml(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "job.yaml"
        p.write_text(body, encoding="utf-8")
        return p

    def test_reference_media_accepted(self, tmp_path):
        (tmp_path / "ref.mp4").write_bytes(b"x")
        cfg = load_job_config(
            self._write_yaml(
                tmp_path,
                "params:\n  reference_media:\n    - path: ref.mp4\n"
                "      kind: video\n      usage: pacing\n      note: lic\n",
            )
        )
        items = cfg.params.reference_media
        assert len(items) == 1
        # Relative path resolved against the job.yaml directory.
        assert Path(items[0].path) == (tmp_path / "ref.mp4").resolve()
        assert items[0].usage == "pacing"
        assert items[0].note == "lic"

    def test_merge_preserves_reference_media(self, tmp_path):
        (tmp_path / "ref.mp4").write_bytes(b"x")
        cfg = load_job_config(
            self._write_yaml(
                tmp_path,
                "movie: M\nparams:\n  reference_media:\n    - path: ref.mp4\n",
            )
        )
        resolved = merge_job({"movie": "M"}, cfg, Settings())
        assert "reference_media" in resolved.params
        assert isinstance(resolved.params["reference_media"], tuple)
        assert resolved.params["reference_media"][0].kind == "video"

    def test_merge_drops_empty_default(self, tmp_path):
        """No reference_media in YAML → key absent from merged params."""
        cfg = load_job_config(self._write_yaml(tmp_path, "movie: M\n"))
        resolved = merge_job({"movie": "M"}, cfg, Settings())
        assert "reference_media" not in resolved.params


# ── 3. Resolve-step validation ────────────────────────────


class TestResolveValidation:
    def test_absent_reference_media_is_noop(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        out = resolve_video(ctx)
        assert "reference_media" not in ctx.metadata
        assert out is ctx

    def test_missing_file_hard_fails(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.metadata["reference_media"] = (
            ReferenceMediaItem(path=str(tmp_path / "nope.mp4")),
        )
        with pytest.raises(FileNotFoundError, match="reference_media\\[0\\]"):
            resolve_video(ctx)

    def test_video_kind_rejects_image_extension(self, tmp_path):
        img = tmp_path / "poster.png"
        img.write_bytes(b"x")
        ctx = _make_ctx(tmp_path)
        ctx.metadata["reference_media"] = [ReferenceMediaItem(path=str(img), kind="video")]
        with pytest.raises(ValueError, match="extension"):
            resolve_video(ctx)

    def test_image_kind_rejects_video_extension(self, tmp_path):
        vid = _write_ref_video(tmp_path)
        ctx = _make_ctx(tmp_path)
        ctx.metadata["reference_media"] = [ReferenceMediaItem(path=vid, kind="image")]
        with pytest.raises(ValueError, match="kind='image'"):
            resolve_video(ctx)

    def test_valid_entries_stored_as_dicts_with_note(self, tmp_path):
        vid = _write_ref_video(tmp_path)
        img = tmp_path / "poster.jpg"
        img.write_bytes(b"x")
        ctx = _make_ctx(tmp_path)
        ctx.metadata["reference_media"] = [
            ReferenceMediaItem(path=vid, usage="pacing", note="self-owned clip"),
            ReferenceMediaItem(path=str(img), kind="image", usage="palette"),
        ]
        resolve_video(ctx)
        entries = ctx.metadata["reference_media"]
        assert isinstance(entries, list) and len(entries) == 2
        assert entries[0] == {
            "path": str(Path(vid).resolve()),
            "kind": "video",
            "usage": "pacing",
            "note": "self-owned clip",
        }
        assert entries[1]["kind"] == "image"
        assert entries[1]["note"] == ""

    def test_dict_entries_accepted_resume_path(self, tmp_path):
        """Resumed contexts carry plain dicts — must re-validate cleanly."""
        vid = _write_ref_video(tmp_path)
        ctx = _make_ctx(tmp_path)
        ctx.metadata["reference_media"] = [{"path": vid, "kind": "video"}]
        resolve_video(ctx)
        assert ctx.metadata["reference_media"][0]["path"] == str(Path(vid).resolve())

    def test_validation_runs_before_video_lookup(self, tmp_path):
        """A bad reference fails even when the video arg is also invalid."""
        ctx = _make_ctx(tmp_path)
        ctx.metadata["video_arg"] = str(tmp_path / "also-missing.mp4")
        ctx.metadata["reference_media"] = [ReferenceMediaItem(path=str(tmp_path / "x.mp4"))]
        with pytest.raises(FileNotFoundError, match="reference_media"):
            resolve_video(ctx)


# ── 4. Prompt augmentation ────────────────────────────────


class TestPromptAugmentation:
    def test_hint_block_empty_when_no_reference_media(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        assert _build_reference_media_hints(ctx) == ""

    def test_hint_block_contains_usage_and_note(self, tmp_path):
        vid = _write_ref_video(tmp_path)
        ctx = _make_ctx(tmp_path)
        ctx.metadata["reference_media"] = [
            {"path": vid, "kind": "video", "usage": "pacing", "note": "my clip"}
        ]
        block = _build_reference_media_hints(ctx)
        assert "Reference style hints" in block
        assert "ref.mp4" in block
        assert "pacing" in block
        assert "my clip" in block

    def test_phase1_prompt_receives_hints_fake_llm(self, tmp_path):
        vid = _write_ref_video(tmp_path)
        ctx = _make_ctx(tmp_path)
        ctx.metadata["reference_media"] = [
            {"path": vid, "kind": "video", "usage": "pacing", "note": "my clip"}
        ]
        ctx.metadata["prompt_target_sentences"] = 3
        mock_cm = _mock_llm_cm(response=_mock_llm_response(_beats_json(3)))
        with patch(
            "movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()
        ):
            _generate_plot_beats(ctx, _mock_settings(), mock_cm.__enter__.return_value, 3)
        prompt = mock_cm.__enter__.return_value.client.chat.completions.create.call_args.kwargs[
            "messages"
        ][0]["content"]
        assert "Reference style hints" in prompt
        assert "pacing" in prompt
        assert "my clip" in prompt

    def test_empty_default_byte_identical_prompt(self, tmp_path):
        """Without reference media the Phase 1 prompt is byte-identical."""

        def _run_prompt(metadata):
            ctx = _make_ctx(tmp_path)
            ctx.metadata.update(metadata)
            ctx.metadata["prompt_target_sentences"] = 3
            mock_cm = _mock_llm_cm(response=_mock_llm_response(_beats_json(3)))
            with patch(
                "movie_narrator.pipeline.script.get_settings",
                return_value=_mock_settings(),
            ):
                _generate_plot_beats(
                    ctx, _mock_settings(), mock_cm.__enter__.return_value, 3
                )
            return (
                mock_cm.__enter__.return_value.client.chat.completions.create.call_args.kwargs[
                    "messages"
                ][0][
                    "content"
                ]
            )

        baseline = _run_prompt({})
        empty_tuple = _run_prompt({"reference_media": ()})
        assert baseline == empty_tuple
        assert "Reference style hints" not in baseline


# ── 5. Image captioning (fake vision provider) ────────────


class _FakeVision(VisionCaptioner):
    """Fake VisionCaptioner recording caption_scenes calls.

    Subclasses the protocol because ``vision_registry`` validates factory
    output (``set_protocol``) — a duck-typed fake would be rejected.
    """

    def __init__(self, captions=None, exc: Exception | None = None):
        self.captions = captions or ["a warm color palette with neon night lights"]
        self.exc = exc
        self.calls = 0

    def caption_scenes(self, scenes, video_path=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return [self.captions[i % len(self.captions)] for i in range(len(scenes))]


# The registry refuses duplicate names and persists across tests, so the
# factory returns a per-test provider via this holder (registered once).
_CURRENT_PROVIDER: dict = {"provider": None}


def _fake_factory(**kwargs):
    return _CURRENT_PROVIDER["provider"]


def _ensure_fake_registered() -> None:
    from movie_narrator.vision.factory import vision_registry

    if not vision_registry.contains("fake_v132"):
        register_vision("fake_v132")(_fake_factory)


class TestImageCaptioning:
    def _ctx_with_image(self, tmp_path, provider_name="fake_v132"):
        img = tmp_path / "poster.jpg"
        img.write_bytes(b"x")
        ctx = _make_ctx(tmp_path)
        ctx.metadata["reference_media"] = [
            {"path": str(img), "kind": "image", "usage": "palette", "note": ""}
        ]
        ctx.metadata["vision_captioner"] = provider_name
        return ctx

    def test_captions_cached_in_metadata(self, tmp_path):
        _CURRENT_PROVIDER["provider"] = _FakeVision()
        _ensure_fake_registered()
        ctx = self._ctx_with_image(tmp_path)
        _maybe_caption_reference_images(ctx)
        captions = ctx.metadata["reference_media_captions"]
        assert len(captions) == 1
        path = list(captions)[0]
        assert "neon night lights" in captions[path]
        # Hint block surfaces the caption.
        block = _build_reference_media_hints(ctx)
        assert "visual:" in block
        assert "neon night lights" in block

    def test_captioned_once_across_calls(self, tmp_path):
        provider = _FakeVision()
        _CURRENT_PROVIDER["provider"] = provider
        _ensure_fake_registered()
        ctx = self._ctx_with_image(tmp_path)
        _maybe_caption_reference_images(ctx)
        _maybe_caption_reference_images(ctx)
        assert provider.calls == 1

    def test_failure_soft_degrades(self, tmp_path):
        _CURRENT_PROVIDER["provider"] = _FakeVision(exc=RuntimeError("VLM down"))
        _ensure_fake_registered()
        ctx = self._ctx_with_image(tmp_path)
        _maybe_caption_reference_images(ctx)  # must not raise
        assert ctx.metadata["reference_media_captions"] == {}
        assert "visual:" not in _build_reference_media_hints(ctx)

    def test_no_provider_configured_skips(self, tmp_path):
        _CURRENT_PROVIDER["provider"] = _FakeVision()
        _ensure_fake_registered()
        ctx = self._ctx_with_image(tmp_path, provider_name="none")
        _maybe_caption_reference_images(ctx)
        assert ctx.metadata["reference_media_captions"] == {}

    def test_caption_limit_three(self, tmp_path):
        provider = _FakeVision()
        _CURRENT_PROVIDER["provider"] = provider
        _ensure_fake_registered()
        entries = [
            {
                "path": str(tmp_path / f"p{i}.jpg"),
                "kind": "image",
                "usage": "style",
                "note": "",
            }
            for i in range(5)
        ]
        for e in entries:
            Path(e["path"]).write_bytes(b"x")
        ctx = _make_ctx(tmp_path)
        ctx.metadata["reference_media"] = entries
        ctx.metadata["vision_captioner"] = "fake_v132"
        _maybe_caption_reference_images(ctx)
        assert provider.calls == 3

    def test_no_images_no_caption_key(self, tmp_path):
        _CURRENT_PROVIDER["provider"] = _FakeVision()
        _ensure_fake_registered()
        vid = _write_ref_video(tmp_path)
        ctx = _make_ctx(tmp_path)
        ctx.metadata["reference_media"] = [{"path": vid, "kind": "video"}]
        ctx.metadata["vision_captioner"] = "fake_v132"
        _maybe_caption_reference_images(ctx)
        assert "reference_media_captions" not in ctx.metadata

    def test_placeholder_captions_filtered(self, tmp_path):
        """Stub-style placeholder labels are never injected as hints."""
        _CURRENT_PROVIDER["provider"] = _FakeVision(captions=["scene 0 from 0.0s to 1.0s"])
        _ensure_fake_registered()
        ctx = self._ctx_with_image(tmp_path)
        _maybe_caption_reference_images(ctx)
        assert ctx.metadata["reference_media_captions"] == {}


# ── 6. End-to-end script step with reference media ────────


class TestGenerateScriptIntegration:
    def test_generate_script_with_reference_media(self, tmp_path):
        vid = _write_ref_video(tmp_path)
        ctx = _make_ctx(tmp_path)
        ctx.metadata["prompt_target_sentences"] = 3
        ctx.metadata["reference_media"] = [
            {"path": vid, "kind": "video", "usage": "style", "note": "lic note"}
        ]
        beats = _mock_llm_response(_beats_json(3))
        segs = _mock_llm_response(_segments_json(["s1", "s2", "s3"]))
        mock_cm = _mock_llm_cm(side_effect=[beats, segs])
        with patch(
            "movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()
        ):
            with patch(
                "movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm
            ):
                result = generate_script(ctx)
        assert result.metadata["script_source"] == "llm"
        assert len(result.segments) == 3
        phase1_prompt = (
            mock_cm.__enter__.return_value.client.chat.completions.create.call_args_list[0]
            .kwargs["messages"][0]["content"]
        )
        assert "Reference style hints" in phase1_prompt
        assert "lic note" in phase1_prompt
