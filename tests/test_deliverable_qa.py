"""Tests for utils/deliverable_qa.py — probe + evaluate with mocked ffmpeg."""

import json
from unittest.mock import patch

import pytest

from movie_narrator.utils.deliverable_qa import (
    QAIssue,
    evaluate_deliverable,
    probe_media,
)


def _ffprobe_json(duration=10.0, has_video=True, has_audio=True, w=1920, h=1080):
    streams = []
    if has_video:
        streams.append({"codec_type": "video", "width": w, "height": h, "duration": str(duration)})
    if has_audio:
        streams.append({"codec_type": "audio", "duration": str(duration)})
    return {"streams": streams, "format": {"duration": str(duration)}}


def test_probe_media_uses_ffprobe(tmp_path):
    """probe_media returns parsed ffprobe JSON when ffprobe is available."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 100)

    def fake_run(cmd, **kw):
        if "ffprobe" in str(cmd[0]):
            return type("P", (), {"returncode": 0, "stdout": json.dumps(_ffprobe_json(12.0)), "stderr": ""})()
        # volumedetect call
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": "mean_volume: -16.0 dB"})()

    with patch("movie_narrator.utils.deliverable_qa.shutil.which", lambda x: "/usr/bin/" + x if x in ("ffprobe", "ffmpeg") else None), \
         patch("movie_narrator.utils.deliverable_qa.subprocess.run", side_effect=fake_run):
        result = probe_media(str(f))

    assert result["duration"] == 12.0
    assert result["has_video"] is True
    assert result["has_audio"] is True
    assert result["width"] == 1920
    assert result["mean_volume"] == -16.0


def test_probe_media_falls_back_to_ffmpeg(tmp_path):
    """When ffprobe is missing, fall back to ffmpeg -i stderr parsing."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 100)

    stderr = (
        "  Duration: 00:00:15.00, start: 0.000000, bitrate: 100 kb/s\n"
        "    Stream #0:0: Video: h264, 1280x720\n"
        "    Stream #0:1: Audio: aac\n"
        "mean_volume: -20.0 dB"
    )

    def fake_run(cmd, **kw):
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": stderr})()

    with patch("movie_narrator.utils.deliverable_qa.shutil.which", lambda x: None), \
         patch("movie_narrator.utils.deliverable_qa.subprocess.run", side_effect=fake_run), \
         patch("movie_narrator.utils.deliverable_qa._ffmpeg_bin", return_value="ffmpeg"):
        result = probe_media(str(f))

    assert result["duration"] == 15.0
    assert result["has_video"] is True
    assert result["has_audio"] is True
    assert result["width"] == 1280
    assert result["height"] == 720
    assert result["mean_volume"] == -20.0


def test_evaluate_missing_file():
    report = evaluate_deliverable("/nonexistent/path.mp4", expected_duration=10.0)
    assert report.ok is False
    assert any(i.code == "missing_file" for i in report.issues)


def test_evaluate_all_pass(tmp_path):
    f = tmp_path / "good.mp4"
    f.write_bytes(b"x" * 50000)
    with patch("movie_narrator.utils.deliverable_qa.probe_media", return_value={
        "duration": 10.0, "has_video": True, "has_audio": True,
        "width": 1920, "height": 1080, "size_bytes": 50000, "mean_volume": -14.0,
    }):
        report = evaluate_deliverable(str(f), expected_duration=10.0)
    assert report.ok is True
    assert report.issues == []


def test_evaluate_too_short(tmp_path):
    f = tmp_path / "short.mp4"
    f.write_bytes(b"x" * 50000)
    with patch("movie_narrator.utils.deliverable_qa.probe_media", return_value={
        "duration": 5.0, "has_video": True, "has_audio": True,
        "width": 1920, "height": 1080, "size_bytes": 50000, "mean_volume": -14.0,
    }):
        report = evaluate_deliverable(str(f), expected_duration=10.0)
    assert report.ok is False
    assert any(i.code == "too_short" for i in report.issues)


def test_evaluate_too_long(tmp_path):
    f = tmp_path / "long.mp4"
    f.write_bytes(b"x" * 50000)
    with patch("movie_narrator.utils.deliverable_qa.probe_media", return_value={
        "duration": 20.0, "has_video": True, "has_audio": True,
        "width": 1920, "height": 1080, "size_bytes": 50000, "mean_volume": -14.0,
    }):
        report = evaluate_deliverable(str(f), expected_duration=10.0)
    assert report.ok is False
    assert any(i.code == "too_long" for i in report.issues)


def test_evaluate_silent_audio(tmp_path):
    f = tmp_path / "silent.mp4"
    f.write_bytes(b"x" * 50000)
    with patch("movie_narrator.utils.deliverable_qa.probe_media", return_value={
        "duration": 10.0, "has_video": True, "has_audio": True,
        "width": 1920, "height": 1080, "size_bytes": 50000, "mean_volume": -60.0,
    }):
        report = evaluate_deliverable(str(f), expected_duration=10.0, max_silence_db=-50.0)
    assert report.ok is False
    assert any(i.code == "silent_audio" for i in report.issues)


def test_evaluate_no_audio_stream(tmp_path):
    f = tmp_path / "noaudio.mp4"
    f.write_bytes(b"x" * 50000)
    with patch("movie_narrator.utils.deliverable_qa.probe_media", return_value={
        "duration": 10.0, "has_video": True, "has_audio": False,
        "width": 1920, "height": 1080, "size_bytes": 50000, "mean_volume": None,
    }):
        report = evaluate_deliverable(str(f), expected_duration=10.0)
    assert report.ok is False
    assert any(i.code == "no_audio_stream" for i in report.issues)


def test_evaluate_tiny_file(tmp_path):
    f = tmp_path / "tiny.mp4"
    f.write_bytes(b"x" * 100)
    with patch("movie_narrator.utils.deliverable_qa.probe_media", return_value={
        "duration": 10.0, "has_video": True, "has_audio": True,
        "width": 1920, "height": 1080, "size_bytes": 100, "mean_volume": -14.0,
    }):
        report = evaluate_deliverable(str(f), expected_duration=10.0, min_size_bytes=10000)
    assert report.ok is False
    assert any(i.code == "tiny_file" for i in report.issues)
