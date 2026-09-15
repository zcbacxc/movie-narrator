# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""M1 architecture boundary tests.

Guards the import-graph cleanup from IMPLEMENTATION_PLAN.md rev.4.20:

- target edges ``contract ↔ plugin_loader`` must stay gone
- ``pipeline.runner`` must not take ``__version__`` from the package root
- ``plugins.contracts`` / ``plugins.discovery`` must stay neutral
  (no import of ``contract`` or ``plugin_loader``)

Uses :func:`collect_edges` from ``scripts/analyze_import_graph.py``
when importable; otherwise falls back to a minimal AST walk over
``src/movie_narrator``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "movie_narrator"
SCRIPTS_DIR = REPO_ROOT / "scripts"

FORBIDDEN_EDGES = {
    ("contract", "plugin_loader"),
    ("plugin_loader", "contract"),
}

# Neutral plugin modules must not reach back into the cycle endpoints.
NEUTRAL_MODULES = ("plugins.contracts", "plugins.discovery")
NEUTRAL_FORBIDDEN_DSTS = ("contract", "plugin_loader")


def _load_collect_edges():
    """Import collect_edges from scripts/ if available."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from analyze_import_graph import collect_edges  # type: ignore

        return collect_edges
    except Exception:  # pragma: no cover - script always present in repo
        return None


def _ast_local_imports(module_rel: str) -> set[str]:
    """Return dotted local destinations imported by one module (AST fallback)."""
    path = PACKAGE_ROOT / module_rel.replace(".", "/")
    if path.is_dir():
        path = path / "__init__.py"
    else:
        path = path.with_suffix(".py")
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dests: set[str] = set()
    parts = module_rel.split(".") if module_rel else []
    if path.name == "__init__.py":
        pkg = parts
    else:
        pkg = parts[:-1] if parts else []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            level = node.level or 0
            if level > 0:
                drop = level - 1
                base = pkg[: len(pkg) - drop] if drop <= len(pkg) else []
                if node.module:
                    dests.add(".".join(base + node.module.split(".")))
                else:
                    for n in names:
                        dests.add(".".join(base + [n]))
            elif node.module and (
                node.module == "movie_narrator"
                or node.module.startswith("movie_narrator.")
            ):
                if node.module == "movie_narrator":
                    if "__version__" in names:
                        dests.add("movie_narrator.__version__")
                else:
                    dests.add(node.module.split(".", 1)[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("movie_narrator."):
                    dests.add(alias.name.split(".", 1)[1])
    return dests


@pytest.fixture(scope="module")
def graph_edges():
    """Current production import edges as a set of (src, dst)."""
    collect_edges = _load_collect_edges()
    if collect_edges is not None:
        result = collect_edges()
        return {(e.src, e.dst) for e in result.edges}
    # AST fallback: only check the modules this test cares about.
    edges: set[tuple[str, str]] = set()
    for src in ("contract", "plugin_loader", "pipeline.runner", *NEUTRAL_MODULES):
        for dst in _ast_local_imports(src):
            edges.add((src, dst))
    return edges


class TestM1TargetEdgesGone:
    """The three M1 target edges must remain absent."""

    def test_contract_plugin_loader_cycle_removed(self, graph_edges):
        for edge in FORBIDDEN_EDGES:
            assert edge not in graph_edges, f"M1 target edge still present: {edge}"

    def test_runner_does_not_import_package_version(self):
        """runner must resolve __version__ via importlib.metadata, not the package root."""
        runner_path = PACKAGE_ROOT / "pipeline" / "runner.py"
        source = runner_path.read_text(encoding="utf-8")
        assert "from .. import __version__" not in source
        assert "from movie_narrator import __version__" not in source
        # Positive: importlib.metadata is the approved resolution path.
        assert "importlib.metadata" in source


class TestPluginsPackageNeutral:
    """plugins.contracts / plugins.discovery must not import the cycle endpoints."""

    @pytest.mark.parametrize("module", NEUTRAL_MODULES)
    def test_neutral_module_avoids_contract_and_plugin_loader(self, module, graph_edges):
        for dst in NEUTRAL_FORBIDDEN_DSTS:
            assert (module, dst) not in graph_edges, (
                f"{module} must not import {dst} (M1 neutrality)"
            )

    def test_neutral_modules_exist(self):
        for module in NEUTRAL_MODULES:
            path = PACKAGE_ROOT.joinpath(*module.split(".")).with_suffix(".py")
            assert path.exists(), f"expected {path}"


class TestPublicSurfacePreserved:
    """126 / 55 acceptance invariants for the M1 refactor."""

    def test_contract_all_length(self):
        from movie_narrator import contract

        assert len(contract.__all__) == 126

    def test_init_imports_55_from_contract(self):
        init_path = PACKAGE_ROOT / "__init__.py"
        tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
        count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "contract":
                count += len(node.names)
        assert count == 55

    def test_plugin_loader_compat_identity(self):
        """Compat wrapper must re-export the same objects contract exposes."""
        from movie_narrator import contract
        from movie_narrator import plugin_loader

        assert contract.load_plugin is plugin_loader.load_plugin
        assert contract.discover_plugins is plugin_loader.discover_plugins
        assert contract.Plugin is plugin_loader.Plugin
