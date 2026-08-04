# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for GAP-5: format -> video_format rename (v0.8.0)."""

from __future__ import annotations

import warnings

import pytest


from movie_narrator.cloud.models import TaskRequest
from movie_narrator.workflow.schema import JobConfig, ResolvedJob
from movie_narrator.contract import CONTRACT_VERSION


class TestTaskRequestFormatRename:
    """Tests for TaskRequest field rename with backward compat."""

    def test_video_format_field_exists(self):
        """TaskRequest has video_format field."""
        req = TaskRequest(movie_name="Test", video_format="9:16")
        assert req.video_format == "9:16"

    def test_default_video_format(self):
        """Default video_format is 16:9."""
        req = TaskRequest(movie_name="Test")
        assert req.video_format == "16:9"

    def test_backward_compat_format_alias(self):
        """Old 'format' key still works via alias."""
        req = TaskRequest(movie_name="Test", format="9:16")
        assert req.video_format == "9:16"

    def test_serialization_uses_video_format(self):
        """Serialization uses the new field name."""
        req = TaskRequest(movie_name="Test", video_format="9:16")
        data = req.model_dump(mode="json")
        assert "video_format" in data
        assert "format" not in data


class TestJobConfigFormatRename:
    """Tests for JobConfig field rename."""

    def test_video_format_field(self):
        """JobConfig accepts video_format."""
        config = JobConfig(video_format="9:16")
        assert config.video_format == "9:16"

    def test_video_format_validator(self):
        """Validator still works with new name."""
        with pytest.raises(ValueError, match="format"):
            JobConfig(video_format="invalid")

    def test_video_format_none_allowed(self):
        """None is allowed for video_format."""
        config = JobConfig()
        assert config.video_format is None


class TestResolvedJobFormatRename:
    """Tests for ResolvedJob field rename."""

    def test_video_format_field(self):
        """ResolvedJob has video_format field."""
        job = ResolvedJob(movie="Test", style="", duration=60, video_format="9:16")
        assert job.video_format == "9:16"


class TestContractVersionBump:
    """Tests for CONTRACT_VERSION bump."""

    def test_contract_version_is_0_8_0(self):
        """CONTRACT_VERSION is (1, 0, 0)."""
        assert CONTRACT_VERSION == (1, 0, 0)
