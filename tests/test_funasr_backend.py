# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the FunASR Chinese ASR provider (G6).

Covers the ``wx_segments`` shaping, the segment-merge logic, backend
selection integration, and the align dispatch path — all with FunASR
itself mocked away so no network / model download is required.
"""

from unittest.mock import MagicMock

import pytest

from movie_narrator.pipeline._align_backend import select_align_backend
from movie_narrator.providers.asr.funasr import (
    FunASRProvider,
    FunASRUnavailable,
    _segments_from_timestamps,
    transcribe_with_funasr,
)


# ── Segment merging ──────────────────────────────────────


def test_segments_empty_text():
    assert _segments_from_timestamps("", None) == []
    assert _segments_from_timestamps("   ", []) == []


def test_segments_single_no_boundary():
    text = "你好世界"
    ts = [[0, 0.2], [0.2, 0.4], [0.4, 0.6], [0.6, 0.8]]
    assert _segments_from_timestamps(text, ts) == [
        {"start": 0.0, "end": 0.8, "text": "你好世界"}
    ]


def test_segments_splits_on_boundaries():
    text = "你好，世界。再见！"
    ts = [
        [0, 0.2], [0.2, 0.4],  # 你 好
        [0.4, 0.6], [0.6, 0.8],  # ， 世
        [0.8, 1.0], [1.0, 1.2],  # 界 。
        [1.2, 1.4], [1.4, 1.6],  # 再 见
        [1.6, 1.8],  # ！
    ]
    result = _segments_from_timestamps(text, ts)
    assert result == [
        {"start": 0.0, "end": 0.6, "text": "你好，"},  # boundary after ，? no, 逗号 splits at end
        {"start": 0.6, "end": 1.2, "text": "世界。"},
        {"start": 1.2, "end": 1.8, "text": "再见！"},
    ]


def test_segments_length_mismatch_falls_back_single():
    text = "你好"
    ts = [[0, 0.2]]  # length mismatch → single segment spanning
    assert _segments_from_timestamps(text, ts) == [
        {"start": 0.0, "end": 0.2, "text": "你好"}
    ]


# ── Provider ─────────────────────────────────────────────


def test_provider_transcribe_builds_wx_segments(monkeypatch):
    fake_auto = MagicMock()
    fake_auto.generate = MagicMock(
        return_value=[
            {
                "text": "开心，快乐。",
                "timestamp": [
                    [0, 0.2],
                    [0.2, 0.4],
                    [0.4, 0.6],
                    [0.6, 0.8],
                    [0.8, 1.0],
                    [1.0, 1.2],
                ],
            }
        ]
    )

    def fake_build_model(self):
        self._auto_model = fake_auto
        return self._auto_model

    monkeypatch.setattr(FunASRProvider, "_ensure_model", fake_build_model)

    provider = FunASRProvider(device="cpu")
    segments = provider.transcribe("fake.wav")
    assert segments == [
        {"start": 0.0, "end": 0.6, "text": "开心，"},
        {"start": 0.6, "end": 1.2, "text": "快乐。"},
    ]


def test_provider_transcribe_empty_result(monkeypatch):
    fake_auto = MagicMock()
    fake_auto.generate = MagicMock(return_value=[])
    monkeypatch.setattr(
        FunASRProvider, "_ensure_model", lambda self: (setattr(self, "_auto_model", fake_auto) or fake_auto)
    )
    provider = FunASRProvider()
    assert provider.transcribe("fake.wav") == []


def test_provider_passes_hotword(monkeypatch):
    fake_auto = MagicMock()
    fake_auto.generate = MagicMock(return_value=[{"text": "好", "timestamp": [[0, 0.2]]}])
    monkeypatch.setattr(
        FunASRProvider, "_ensure_model", lambda self: (setattr(self, "_auto_model", fake_auto) or fake_auto)
    )
    provider = FunASRProvider()
    provider.transcribe("fake.wav", hotword="飞驰人生")
    kwargs = fake_auto.generate.call_args.kwargs
    assert kwargs["hotword"] == "飞驰人生"
    assert kwargs["batch_size_s"] == 300


def test_provider_raises_when_funasr_missing(monkeypatch):
    monkeypatch.setattr(
        FunASRProvider,
        "_ensure_model",
        lambda self: (_ for _ in ()).throw(FunASRUnavailable("funasr not installed")),
    )
    provider = FunASRProvider()
    with pytest.raises(FunASRUnavailable):
        provider.transcribe("fake.wav")


def test_transcribe_with_funasr_wrapper(monkeypatch):
    def fake_transcribe(self, audio_path, language="zh", hotword=None, batch_size_s=300):
        return [{"start": 0.0, "end": 1.0, "text": "测试"}]

    monkeypatch.setattr(FunASRProvider, "transcribe", fake_transcribe)
    assert transcribe_with_funasr("fake.wav") == [{"start": 0.0, "end": 1.0, "text": "测试"}]


# ── Backend selection integration ────────────────────────


def _make_ctx(tmp_path):
    from movie_narrator.models import Context, TimedSegment

    out_dir = str(tmp_path) if tmp_path is not None else "output"
    ctx = Context(
        movie_name="m",
        output_dir=out_dir,
        audio_path="a.wav" if tmp_path is not None else None,
        timed_segments=[TimedSegment(text="a", start=0.0, end=2.0)],
    )
    ctx.metadata = {}
    return ctx


def test_select_funasr_when_only_funasr_available(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path)

    def fake_probe(name):
        return (name == "funasr", "")

    monkeypatch.setattr("movie_narrator.pipeline._align_backend.probe", fake_probe)
    monkeypatch.setattr("movie_narrator.pipeline._align_backend.platform.system", lambda: "Linux")

    backend, reason = select_align_backend(ctx)
    assert backend == "funasr"
    assert "funasr" in reason


def test_select_funasr_windows_when_faster_whisper_missing(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path)
    ctx.metadata["whisperx_device"] = "cpu"

    def fake_probe(name):
        return ({"whisperx": True, "faster_whisper": False, "funasr": True}.get(name, False), "")

    monkeypatch.setattr("movie_narrator.pipeline._align_backend.probe", fake_probe)
    monkeypatch.setattr("movie_narrator.pipeline._align_backend.platform.system", lambda: "Windows")

    backend, reason = select_align_backend(ctx)
    assert backend == "funasr"
    assert "Windows" in reason


def test_select_funasr_explicit_override(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path)
    ctx.metadata["align_backend"] = "funasr"
    monkeypatch.setattr("movie_narrator.pipeline._align_backend.probe", lambda name: (True, ""))
    backend, reason = select_align_backend(ctx)
    assert backend == "funasr"
    assert "override" in reason


def test_faster_whisper_still_preferred_when_available(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path)
    monkeypatch.setattr(
        "movie_narrator.pipeline._align_backend.probe", lambda name: (True, "")
    )
    monkeypatch.setattr("movie_narrator.pipeline._align_backend.platform.system", lambda: "Windows")
    ctx.metadata["whisperx_device"] = "cpu"
    backend, _ = select_align_backend(ctx)
    assert backend == "faster_whisper"


def test_run_funasr_maps_metadata(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path)
    ctx.metadata["whisperx_device"] = "cuda"
    ctx.metadata["funasr_model"] = "my-model"
    ctx.metadata["funasr_hotword"] = "热词"

    captured = {}

    def fake_transcribe(audio_path, device="cpu", model=None, model_dir=None, hotword=None):
        captured.update(
            {"audio": audio_path, "device": device, "model": model, "hotword": hotword}
        )
        return [{"start": 0.0, "end": 1.0, "text": "ok"}]

    monkeypatch.setattr(
        "movie_narrator.pipeline._align_backend.transcribe_with_funasr", fake_transcribe
    )
    from movie_narrator.pipeline._align_backend import run_funasr

    assert run_funasr(ctx) == [{"start": 0.0, "end": 1.0, "text": "ok"}]
    assert captured["device"] == "cuda"
    assert captured["model"] == "my-model"
    assert captured["hotword"] == "热词"
