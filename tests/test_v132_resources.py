# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the v1.3.2 resource-aware render admission (Feature 11).

Covers: the temp-space heuristic formula and its clamps, the admission
check (sufficient / insufficient / min-floor / disk_usage failure /
CPU advisory), the opt-in env flag semantics, and the render step
raising when the preflight is enabled and space is insufficient.
"""

import shutil
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Context, Services
from movie_narrator.pipeline.render import render_video
from movie_narrator.utils.resources import (
    AdmissionCheck,
    check_render_admission,
    env_flag_enabled,
    estimate_temp_space_bytes,
)

GB = 1024 * 1024 * 1024


def _fake_ctx(tmp_path, **kw):
    defaults = dict(
        movie_name="test_movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )
    defaults.update(kw)
    return Context(**defaults)


def _patch_disk_usage(monkeypatch, free_bytes: int, tmp_path):
    """Patch shutil.disk_usage inside utils.resources (as imported)."""

    class _Usage:
        free = free_bytes
        total = free_bytes * 2
        used = free_bytes

    monkeypatch.setattr(
        "movie_narrator.utils.resources.shutil.disk_usage",
        lambda _path: _Usage(),
    )
    return tmp_path


# ── 1. Heuristic formula + bounds ─────────────────────────


class TestFormula:
    def test_base_1080p_60s_is_2gb(self):
        need = estimate_temp_space_bytes((1920, 1080), 60.0)
        assert 1.9 * GB < need < 2.1 * GB

    def test_scales_linearly_with_area_and_duration(self):
        base = estimate_temp_space_bytes((1920, 1080), 60.0)
        # 4K doubles BOTH dimensions → 4x the frame area → 4x the need.
        fourk = estimate_temp_space_bytes((3840, 2160), 60.0)
        double_duration = estimate_temp_space_bytes((1920, 1080), 120.0)
        assert 7.9 * GB < fourk < 8.1 * GB
        assert 3.9 * GB < double_duration < 4.1 * GB
        assert fourk == 4 * base
        assert double_duration == 2 * base

    def test_clamped_low(self):
        need = estimate_temp_space_bytes((320, 180), 1.0)
        assert need == int(0.5 * GB)

    def test_clamped_high(self):
        need = estimate_temp_space_bytes((7680, 4320), 3600.0)
        assert need == int(50 * GB)

    def test_non_positive_duration_falls_back_to_floor(self):
        assert estimate_temp_space_bytes((1920, 1080), 0.0) == int(0.5 * GB)
        assert estimate_temp_space_bytes((1920, 1080), -5.0) == int(0.5 * GB)


# ── 2. Admission check ────────────────────────────────────


class TestCheckRenderAdmission:
    def test_ok_when_plenty_of_space(self, tmp_path, monkeypatch):
        _patch_disk_usage(monkeypatch, free_bytes=100 * GB, tmp_path=tmp_path)
        check = check_render_admission(
            resolution=(1920, 1080),
            duration_estimate_s=60.0,
            temp_dir=tmp_path,
        )
        assert check.ok is True
        assert check.reasons == ()

    def test_fails_when_insufficient(self, tmp_path, monkeypatch):
        _patch_disk_usage(monkeypatch, free_bytes=int(0.2 * GB), tmp_path=tmp_path)
        check = check_render_admission(
            resolution=(1920, 1080),
            duration_estimate_s=60.0,
            temp_dir=tmp_path,
        )
        assert check.ok is False
        assert any("insufficient disk space" in r for r in check.reasons)
        # The message includes the numbers.
        joined = "; ".join(check.reasons)
        assert "GB" in joined and str(tmp_path) in joined

    def test_min_free_floor_tightens_requirement(self, tmp_path, monkeypatch):
        # Free space covers the heuristic (~2 GB) but not the user floor.
        _patch_disk_usage(monkeypatch, free_bytes=int(3 * GB), tmp_path=tmp_path)
        check = check_render_admission(
            resolution=(1920, 1080),
            duration_estimate_s=60.0,
            temp_dir=tmp_path,
            min_free_disk_bytes=10 * GB,
        )
        assert check.ok is False
        # Conversely: floor satisfied, heuristic satisfied → ok.
        _patch_disk_usage(monkeypatch, free_bytes=20 * GB, tmp_path=tmp_path)
        check2 = check_render_admission(
            resolution=(1920, 1080),
            duration_estimate_s=60.0,
            temp_dir=tmp_path,
            min_free_disk_bytes=10 * GB,
        )
        assert check2.ok is True

    def test_disk_usage_failure_is_hard_failure(self, tmp_path, monkeypatch):
        def _boom(_path):
            raise OSError("volume does not exist")

        monkeypatch.setattr(
            "movie_narrator.utils.resources.shutil.disk_usage", _boom
        )
        check = check_render_admission(
            resolution=(1920, 1080),
            duration_estimate_s=60.0,
            temp_dir=tmp_path,
        )
        assert check.ok is False
        assert any("cannot determine free disk space" in r for r in check.reasons)

    def test_cpu_count_is_advisory_only(self, tmp_path, monkeypatch):
        _patch_disk_usage(monkeypatch, free_bytes=100 * GB, tmp_path=tmp_path)
        check = check_render_admission(
            resolution=(1920, 1080),
            duration_estimate_s=60.0,
            temp_dir=tmp_path,
            cpu_count=1,
        )
        assert check.ok is True
        assert any(r.startswith("advisory:") for r in check.reasons)


# ── 3. Env flag semantics ─────────────────────────────────


class TestEnvFlag:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("MN_ADMISSION_DISK_CHECK", raising=False)
        assert env_flag_enabled("MN_ADMISSION_DISK_CHECK") is False

    def test_truthy_values(self, monkeypatch):
        for val in ("1", "true", "YES", " on "):
            monkeypatch.setenv("MN_ADMISSION_DISK_CHECK", val)
            assert env_flag_enabled("MN_ADMISSION_DISK_CHECK") is True
        for val in ("0", "false", "", "no"):
            monkeypatch.setenv("MN_ADMISSION_DISK_CHECK", val)
            assert env_flag_enabled("MN_ADMISSION_DISK_CHECK") is False


# ── 4. Render step integration ────────────────────────────


class TestRenderStepPreflight:
    def test_disabled_by_default_check_not_called(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MN_ADMISSION_DISK_CHECK", raising=False)
        check_mock = MagicMock(return_value=AdmissionCheck(ok=False, reasons=("x",)))
        with patch(
            "movie_narrator.pipeline.render.check_render_admission", check_mock
        ):
            with pytest.raises(Exception) as exc_info:
                render_video(_fake_ctx(tmp_path))
            # The failure must come from the render body, not the preflight.
            assert "Render admission check failed" not in str(exc_info.value)
        check_mock.assert_not_called()

    def test_enabled_insufficient_space_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MN_ADMISSION_DISK_CHECK", "1")
        check_mock = MagicMock(
            return_value=AdmissionCheck(
                ok=False,
                reasons=("insufficient disk space for render: need ~2.0 GB, only 0.1 GB free",),
            )
        )
        with patch(
            "movie_narrator.pipeline.render.check_render_admission", check_mock
        ):
            with pytest.raises(RuntimeError, match="Render admission check failed"):
                render_video(_fake_ctx(tmp_path))
        # The preflight receives the ctx-derived inputs.
        kwargs = check_mock.call_args.kwargs
        assert kwargs["resolution"] == (1920, 1080)
        assert kwargs["duration_estimate_s"] == 60.0
        assert str(kwargs["temp_dir"]) == str(tmp_path / "cache")

    def test_enabled_ok_proceeds_into_render_body(self, tmp_path, monkeypatch):
        """Preflight passes → the step continues (fails later on fake audio)."""
        monkeypatch.setenv("MN_ADMISSION_DISK_CHECK", "1")
        check_mock = MagicMock(return_value=AdmissionCheck(ok=True, reasons=()))
        with patch(
            "movie_narrator.pipeline.render.check_render_admission", check_mock
        ):
            with pytest.raises(Exception) as exc_info:
                render_video(_fake_ctx(tmp_path))
            assert "Render admission check failed" not in str(exc_info.value)
        check_mock.assert_called_once()
