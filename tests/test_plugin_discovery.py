"""Tests for plugin discovery via entry_points and Services extension.

Covers:
- discover_plugins() with mocked entry points
- list_available_plugins()
- PluginLoadResult dataclass
- _load_entry_point helper (class vs instance)
- Services.logger optional field
- SDK exports for discover_plugins / list_available_plugins
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.plugin_loader import (
    ENTRY_POINT_GROUP,
    PluginLoadResult,
    _load_entry_point,
    discover_plugins,
    list_available_plugins,
)
from movie_narrator.contract import Plugin, PluginContext, load_plugin
from movie_narrator.models import Context, Services, Assets
from movie_narrator.pipeline.registry import step_registry
from movie_narrator.utils.console import SilentConsole


# ── Helpers ───────────────────────────────────────────────


def _make_fake_entry_point(name: str, obj) -> MagicMock:
    """Create a fake EntryPoint that loads *obj*.

    Uses ``MagicMock`` instead of the real ``EntryPoint`` class because
    ``EntryPoint`` became a frozen dataclass in Python 3.11+, making its
    attributes immutable. ``MagicMock`` provides the same duck-typed
    interface (``.name``, ``.group``, ``.load()``) that
    ``_load_entry_point`` and ``discover_plugins`` rely on.
    """
    ep = MagicMock()
    ep.name = name
    ep.group = ENTRY_POINT_GROUP
    ep.value = "fake:Fake"
    ep.load = MagicMock(return_value=obj)
    return ep


@pytest.fixture(autouse=True)
def _clean_step_registry():
    """Auto-cleanup: remove any steps registered during a test.

    Prevents test pollution when a test fails mid-way after calling
    ``load_plugin()`` but before its explicit cleanup runs.
    """
    before = set(step_registry.names())
    yield
    after = set(step_registry.names())
    for name in after - before:
        step_registry.unregister(name)


class FakePlugin:
    """A minimal valid plugin for testing.

    Uses a unique step name per instance to avoid cross-test
    contamination of the global step_registry.
    """

    _counter = 0

    def __init__(self):
        self.registered = False
        FakePlugin._counter += 1
        self.name = f"fake-{FakePlugin._counter}"
        self._step_name = f"fake_step_{FakePlugin._counter}"

    def register(self, ctx: PluginContext) -> None:
        self.registered = True
        # Register a dummy step to verify registry integration
        def my_step(ctx):
            return ctx

        ctx.steps.register(self._step_name, my_step, after="resolve_video")


class FakePluginClass:
    """A plugin class (not instance) for testing class instantiation."""

    name = "fake-class"

    def register(self, ctx: PluginContext) -> None:
        pass


class NotAPlugin:
    """Does not implement the Plugin protocol."""

    pass


# ── PluginLoadResult tests ───────────────────────────────


class TestPluginLoadResult:
    """PluginLoadResult dataclass."""

    def test_success_result(self):
        r = PluginLoadResult(name="test", success=True)
        assert r.name == "test"
        assert r.success is True
        assert r.error == ""

    def test_failure_result(self):
        r = PluginLoadResult(name="bad", success=False, error="boom")
        assert r.name == "bad"
        assert r.success is False
        assert r.error == "boom"


# ── _load_entry_point tests ──────────────────────────────


class TestLoadEntryPoint:
    """_load_entry_point helper function."""

    def test_loads_instance(self):
        plugin = FakePlugin()
        ep = _make_fake_entry_point("fake", plugin)
        loaded = _load_entry_point(ep)
        assert loaded is plugin

    def test_loads_class_and_instantiates(self):
        ep = _make_fake_entry_point("fake-class", FakePluginClass)
        loaded = _load_entry_point(ep)
        assert isinstance(loaded, FakePluginClass)
        assert loaded.name == "fake-class"

    def test_rejects_non_plugin(self):
        ep = _make_fake_entry_point("bad", NotAPlugin())
        with pytest.raises(TypeError, match="does not implement the Plugin protocol"):
            _load_entry_point(ep)


# ── discover_plugins tests ───────────────────────────────


class TestDiscoverPlugins:
    """discover_plugins() function."""

    def test_no_plugins_returns_empty_list(self):
        """When no entry points exist, returns empty list."""
        with patch("movie_narrator.plugin_loader.entry_points") as mock_ep:
            mock_ep.return_value = []
            results = discover_plugins()
            assert results == []

    def test_loads_valid_plugin(self):
        """A valid plugin is loaded and registered."""
        plugin = FakePlugin()
        ep = _make_fake_entry_point(plugin.name, plugin)

        with patch("movie_narrator.plugin_loader.entry_points") as mock_ep:
            mock_ep.return_value = [ep]
            results = discover_plugins()

        assert len(results) == 1
        assert results[0].name == plugin.name
        assert results[0].success is True
        assert plugin.registered is True

        # Cleanup
        step_registry.unregister(plugin._step_name)

    def test_broken_plugin_does_not_block_others(self):
        """A broken plugin produces a failure result but others still load."""
        good_plugin = FakePlugin()
        ep_good = _make_fake_entry_point(good_plugin.name, good_plugin)
        ep_bad = _make_fake_entry_point("bad", NotAPlugin())

        with patch("movie_narrator.plugin_loader.entry_points") as mock_ep:
            mock_ep.return_value = [ep_bad, ep_good]
            results = discover_plugins()

        assert len(results) == 2
        # The broken one should fail
        bad_result = next(r for r in results if r.name == "bad")
        assert bad_result.success is False
        assert "does not implement" in bad_result.error
        # The good one should succeed
        good_result = next(r for r in results if r.name == good_plugin.name)
        assert good_result.success is True

        # Cleanup
        step_registry.unregister(good_plugin._step_name)

    def test_duplicate_plugin_name_fails_gracefully(self):
        """Loading a plugin whose step name is already taken fails gracefully."""
        plugin1 = FakePlugin()
        load_plugin(plugin1)  # Pre-register its step

        plugin2 = FakePlugin()
        # Force plugin2 to use the same step name as plugin1
        plugin2._step_name = plugin1._step_name
        ep1 = _make_fake_entry_point("fake1", plugin1)
        ep2 = _make_fake_entry_point("fake2", plugin2)

        with patch("movie_narrator.plugin_loader.entry_points") as mock_ep:
            mock_ep.return_value = [ep1, ep2]
            results = discover_plugins()

        # First should fail (already loaded), second should fail (duplicate step)
        assert results[0].success is False
        assert "already registered" in results[0].error
        assert results[1].success is False
        assert "already registered" in results[1].error

        # Cleanup
        step_registry.unregister(plugin1._step_name)

    def test_emits_warning_on_failure(self, recwarn):
        """Failed plugin loads emit a UserWarning."""
        ep = _make_fake_entry_point("bad", NotAPlugin())

        with patch("movie_narrator.plugin_loader.entry_points") as mock_ep:
            mock_ep.return_value = [ep]
            discover_plugins()

        assert len(recwarn) >= 1
        assert any("Failed to load plugin" in str(w.message) for w in recwarn)


# ── list_available_plugins tests ─────────────────────────


class TestListAvailablePlugins:
    """list_available_plugins() function."""

    def test_returns_entry_point_names(self):
        ep1 = _make_fake_entry_point("plugin-a", FakePlugin())
        ep2 = _make_fake_entry_point("plugin-b", FakePlugin())

        with patch("movie_narrator.plugin_loader.entry_points") as mock_ep:
            mock_ep.return_value = [ep1, ep2]
            names = list_available_plugins()

        assert "plugin-a" in names
        assert "plugin-b" in names

    def test_empty_when_no_plugins(self):
        with patch("movie_narrator.plugin_loader.entry_points") as mock_ep:
            mock_ep.return_value = []
            names = list_available_plugins()
        assert names == []


# ── Services logger tests ────────────────────────────────


class TestServicesLogger:
    """Services.logger optional field."""

    def test_logger_defaults_to_none(self):
        svc = Services(console=SilentConsole())
        assert svc.logger is None

    def test_logger_can_be_set(self):
        logger = logging.getLogger("test")
        svc = Services(console=SilentConsole(), logger=logger)
        assert svc.logger is logger

    def test_logger_accepts_duck_typed_object(self):
        """Any object with info/warning/error methods works."""
        fake_logger = MagicMock()
        svc = Services(console=SilentConsole(), logger=fake_logger)
        svc.logger.info("test message")
        fake_logger.info.assert_called_once_with("test message")

    def test_context_services_logger_accessible(self):
        """ctx.services.logger is accessible from step functions."""
        fake_logger = MagicMock()
        ctx = Context(
            movie_name="test",
            output_dir="/tmp/test",
            services=Services(console=SilentConsole(), logger=fake_logger),
        )
        assert ctx.services.logger is fake_logger


# ── SDK export tests ─────────────────────────────────────


class TestSDKExports:
    """Verify new SDK symbols are exported from movie_narrator."""

    def test_discover_plugins_exported(self):
        from movie_narrator import discover_plugins
        assert callable(discover_plugins)

    def test_list_available_plugins_exported(self):
        from movie_narrator import list_available_plugins
        assert callable(list_available_plugins)

    def test_services_exported(self):
        from movie_narrator import Services as ExportedServices
        from movie_narrator.models import Services
        assert ExportedServices is Services

    def test_entry_point_group_constant(self):
        assert ENTRY_POINT_GROUP == "movie_narrator.plugins"

    def test_discover_plugins_in_contract_all(self):
        from movie_narrator import contract
        assert "discover_plugins" in contract.__all__
        assert "list_available_plugins" in contract.__all__
        assert "Services" in contract.__all__


# ── Integration: full discovery flow ─────────────────────


class TestDiscoveryIntegration:
    """End-to-end: entry point -> load_plugin -> registry."""

    def test_discovered_plugin_registers_step(self):
        """A plugin discovered via entry_points actually registers a step."""
        plugin = FakePlugin()
        ep = _make_fake_entry_point(plugin.name, plugin)

        with patch("movie_narrator.plugin_loader.entry_points") as mock_ep:
            mock_ep.return_value = [ep]
            results = discover_plugins()

        assert results[0].success is True
        assert step_registry.contains(plugin._step_name)

        # Verify ordering: plugin step should be after resolve_video
        names = step_registry.ordered_names()
        if "resolve_video" in names:
            assert names.index(plugin._step_name) > names.index("resolve_video")

        # Cleanup
        step_registry.unregister(plugin._step_name)

    def test_manual_load_then_discover_idempotency(self):
        """Manually loading then discovering reports duplicate as failure."""
        plugin = FakePlugin()
        load_plugin(plugin)

        ep = _make_fake_entry_point(plugin.name, plugin)
        with patch("movie_narrator.plugin_loader.entry_points") as mock_ep:
            mock_ep.return_value = [ep]
            results = discover_plugins()

        assert results[0].success is False
        assert "already registered" in results[0].error

        # Cleanup
        step_registry.unregister(plugin._step_name)
