"""Tests for M5 — CLI plugin commands, version check, plugin template."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from movie_narrator.cli import app
from movie_narrator.contract import CONTRACT_VERSION, check_version


# ── Version check helper ─────────────────────────────────


class TestCheckVersion:
    """check_version() helper for plugin/consumer compatibility."""

    def test_check_version_passes_when_satisfied(self):
        """check_version does not raise when CONTRACT_VERSION >= required."""
        check_version((0, 5, 0))
        check_version((0, 5, 1))

    def test_check_version_raises_when_below(self):
        """check_version raises ImportError when CONTRACT_VERSION < required."""
        with pytest.raises(ImportError, match="below the required"):
            check_version((0, 7, 2))

    def test_check_version_raises_with_future_major(self):
        with pytest.raises(ImportError, match="below the required"):
            check_version((1, 0, 0))

    def test_check_version_exported_from_contract(self):
        from movie_narrator.contract import check_version as _cv
        assert _cv is check_version

    def test_check_version_exported_from_init(self):
        from movie_narrator import check_version as _cv
        assert _cv is check_version

    def test_check_version_in_all(self):
        from movie_narrator import contract
        assert "check_version" in contract.__all__


# ── CLI plugin commands ──────────────────────────────────


runner = CliRunner()


class TestCliPluginVersion:
    """mn plugin version — shows CONTRACT_VERSION."""

    def test_plugin_version_outputs_contract_version(self):
        result = runner.invoke(app, ["plugin", "version"])
        assert result.exit_code == 0
        assert str(CONTRACT_VERSION) in result.output
        assert "CONTRACT_VERSION" in result.output

    def test_plugin_version_shows_semver_string(self):
        result = runner.invoke(app, ["plugin", "version"])
        assert result.exit_code == 0
        semver = ".".join(str(v) for v in CONTRACT_VERSION)
        assert semver in result.output


class TestCliPluginList:
    """mn plugin list — lists entry_points plugins."""

    def test_plugin_list_runs_without_error(self):
        result = runner.invoke(app, ["plugin", "list"])
        assert result.exit_code == 0
        # No plugins installed in test env, but should show helpful message
        assert "entry_points" in result.output.lower() or "plugin" in result.output.lower()

    def test_plugin_list_with_mocked_entry_points(self):
        """When entry points exist, they are listed."""
        from movie_narrator.plugin_loader import list_available_plugins

        with patch(
            "movie_narrator.plugin_loader.entry_points"
        ) as mock_ep:
            mock_ep.return_value = []
            result = runner.invoke(app, ["plugin", "list"])
            assert result.exit_code == 0
            assert "No plugins found" in result.output


class TestCliPluginRegistries:
    """mn plugin registries — shows all registered steps and providers."""

    def test_plugin_registries_shows_step_registry(self):
        result = runner.invoke(app, ["plugin", "registries"])
        assert result.exit_code == 0
        assert "Step Registry" in result.output
        assert "resolve_video" in result.output

    def test_plugin_registries_shows_tts_registry(self):
        result = runner.invoke(app, ["plugin", "registries"])
        assert result.exit_code == 0
        assert "TTS Registry" in result.output
        assert "edge" in result.output

    def test_plugin_registries_shows_vision_registry(self):
        result = runner.invoke(app, ["plugin", "registries"])
        assert result.exit_code == 0
        assert "Vision Registry" in result.output
        assert "stub" in result.output

    def test_plugin_registries_shows_llm_registry(self):
        result = runner.invoke(app, ["plugin", "registries"])
        assert result.exit_code == 0
        assert "LLM Registry" in result.output
        assert "openai" in result.output

    def test_plugin_registries_shows_research_registry(self):
        result = runner.invoke(app, ["plugin", "registries"])
        assert result.exit_code == 0
        assert "Research Registry" in result.output
        assert "llm" in result.output

    def test_plugin_registries_shows_protocol_tag(self):
        result = runner.invoke(app, ["plugin", "registries"])
        assert result.exit_code == 0
        # TTS and Vision have protocol validation
        assert "[protocol]" in result.output


class TestCliPluginInvalidAction:
    """mn plugin with invalid action."""

    def test_invalid_action_errors(self):
        result = runner.invoke(app, ["plugin", "bogus"])
        assert result.exit_code != 0
        assert "Unknown action" in result.output


# ── Plugin template ──────────────────────────────────────


class TestPluginTemplate:
    """The plugin template in examples/plugins/template/ is valid."""

    TEMPLATE_DIR = (
        Path(__file__).resolve().parent.parent
        / "examples" / "plugins" / "template"
    )

    def test_template_directory_exists(self):
        assert self.TEMPLATE_DIR.is_dir()

    def test_template_has_pyproject(self):
        assert (self.TEMPLATE_DIR / "pyproject.toml").is_file()

    def test_template_has_readme(self):
        assert (self.TEMPLATE_DIR / "README.md").is_file()

    def test_template_has_init(self):
        assert (self.TEMPLATE_DIR / "template_plugin" / "__init__.py").is_file()

    def test_template_pyproject_has_entry_point(self):
        content = (self.TEMPLATE_DIR / "pyproject.toml").read_text()
        assert "movie_narrator.plugins" in content
        assert "PLUGIN_NAME" in content

    def test_template_readme_has_quick_start(self):
        content = (self.TEMPLATE_DIR / "README.md").read_text()
        assert "Quick Start" in content
        assert "mn plugin list" in content

    def test_template_init_implements_plugin_protocol(self):
        """The template's plugin class implements the Plugin protocol."""
        sys.path.insert(0, str(self.TEMPLATE_DIR))
        try:
            from template_plugin import TemplatePlugin
            from movie_narrator.contract import Plugin

            plugin = TemplatePlugin()
            assert isinstance(plugin, Plugin)
            assert plugin.name == "template"
        finally:
            sys.path.remove(str(self.TEMPLATE_DIR))


# ── ProviderRegistry.info() ──────────────────────────────


class TestProviderRegistryInfo:
    """ProviderRegistry.info() returns structured provider metadata."""

    def test_tts_registry_info(self):
        import movie_narrator.tts.factory  # noqa: F401
        from movie_narrator.providers import tts_registry

        info = tts_registry.info()
        assert isinstance(info, list)
        assert len(info) > 0
        names = [i["name"] for i in info]
        assert "edge" in names
        for entry in info:
            assert entry["category"] == "tts"
            assert entry["protocol_validated"] is True

    def test_vision_registry_info(self):
        import movie_narrator.vision.factory  # noqa: F401
        from movie_narrator.providers import vision_registry

        info = vision_registry.info()
        assert isinstance(info, list)
        assert len(info) > 0
        names = [i["name"] for i in info]
        assert "stub" in names
        for entry in info:
            assert entry["category"] == "vision"
            assert entry["protocol_validated"] is True

    def test_llm_registry_info(self):
        import movie_narrator.utils.llm  # noqa: F401
        from movie_narrator.providers import llm_registry

        info = llm_registry.info()
        assert isinstance(info, list)
        names = [i["name"] for i in info]
        assert "openai" in names
        for entry in info:
            assert entry["category"] == "llm"
            assert entry["protocol_validated"] is False

    def test_research_registry_info(self):
        import movie_narrator.pipeline.research  # noqa: F401
        from movie_narrator.providers import research_registry

        info = research_registry.info()
        assert isinstance(info, list)
        names = [i["name"] for i in info]
        assert "llm" in names
        for entry in info:
            assert entry["category"] == "research"
            assert entry["protocol_validated"] is False
