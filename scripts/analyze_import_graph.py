# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Analyze production-package import edges for M0/P0-11 boundary work.

Scope is frozen by IMPLEMENTATION_PLAN.md §2.2:
  production package only (``src/movie_narrator/**/*.py``).
  tests / examples / fixtures / generated are out of scope.

Each production edge gets exactly one ``classification`` and one
``remediation``:

  classification ∈ {architectural, version_debt, excluded}
  remediation    ∈ {target, deferred, excluded}

Baseline SCC / self-loop members are machine-filled — do not hand-edit.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a runtime dep
    yaml = None

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "movie_narrator"
PACKAGE_NAME = "movie_narrator"
VERSION_DEBT_DST = f"{PACKAGE_NAME}.__version__"

# M1 target edges: contract ↔ plugin_loader cycle + runner version-debt.
TARGET_EDGES: Set[Tuple[str, str]] = {
    ("contract", "plugin_loader"),
    ("plugin_loader", "contract"),
    ("pipeline.runner", VERSION_DEBT_DST),
}

# New edges approved by the plan after M1 relocation (not failures).
# The plan frozen list is the four edges from contract/plugin_loader.
# Additional package-internal / source-module edges produced by the
# relocation are one-way into the neutral plugins package and are
# approved so --strict-check can gate M1 without false positives.
APPROVED_NEW_EDGES: Set[Tuple[str, str]] = {
    ("contract", "plugins.contracts"),
    ("contract", "plugins.discovery"),
    ("plugin_loader", "plugins.contracts"),
    ("plugin_loader", "plugins.discovery"),
    # Plan-mandated source imports + package re-exports (one-way, no cycle).
    ("plugins", "plugins.contracts"),
    ("plugins", "plugins.discovery"),
    ("plugins.contracts", "pipeline.registry"),
    ("plugins.contracts", "providers.registry"),
    ("plugins.discovery", "plugins.contracts"),
}


@dataclass
class Edge:
    src: str
    dst: str
    kind: str  # "import" | "from_import" | "delayed"
    classification: str
    remediation: str
    detail: str = ""


@dataclass
class GraphResult:
    edges: List[Edge] = field(default_factory=list)
    self_loops: List[str] = field(default_factory=list)
    version_debt: List[str] = field(default_factory=list)
    sccs: List[List[str]] = field(default_factory=list)
    modules: List[str] = field(default_factory=list)


def module_parts_from_path(path: Path) -> List[str]:
    """Return dotted parts relative to package root, without ``movie_narrator``."""
    rel = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


def local_name(parts: List[str]) -> str:
    return ".".join(parts)


def is_version_debt_names(names: Iterable[str]) -> bool:
    return any(n == "__version__" for n in names)


def resolve_relative_import(
    module_parts: List[str],
    is_package: bool,
    level: int,
    module: Optional[str],
    names: List[str],
) -> List[str]:
    """Resolve a relative ``from`` import to one or more local destination names.

    Returns destination local module names (empty string = package root), or
    ``[VERSION_DEBT_DST]`` for ``from .. import __version__``.
    """
    # Base package of the current module.
    # For file ``a/b/c.py`` (is_package=False): package is ``a.b``.
    # For package ``a/b/__init__.py`` (is_package=True): package is ``a.b``.
    if is_package:
        pkg = list(module_parts)
    else:
        pkg = list(module_parts[:-1]) if module_parts else []

    # level=1 → current package; level=2 → one parent; etc.
    drop = level - 1
    if drop > len(pkg):
        # Walked above package root — clamp to package root.
        pkg = []
    else:
        pkg = pkg[: len(pkg) - drop]

    if is_version_debt_names(names):
        return [VERSION_DEBT_DST]

    if module:
        # from .x.y import z  →  dest is pkg + x.y
        mod_parts = module.split(".")
        dest = pkg + mod_parts
        return [local_name(dest)]

    # from . import a, b  →  each name is a submodule of pkg
    if not names:
        return []
    return [local_name(pkg + [n]) for n in names]


def resolve_absolute_import(module: str, names: List[str]) -> List[str]:
    if is_version_debt_names(names):
        return [VERSION_DEBT_DST]
    if module.startswith(PACKAGE_NAME + ".") or module == PACKAGE_NAME:
        if module == PACKAGE_NAME:
            # from movie_narrator import __version__ → version debt
            return [VERSION_DEBT_DST] if is_version_debt_names(names) else []
        return [local_name(module.split(".")[1:])]
    # ``from movie_narrator import X`` already handled; bare import outside package.
    if module.startswith(PACKAGE_NAME):
        return []
    # Absolute import of another top-level package — out of scope.
    return []


def classification_for(src: str, dst: str) -> Tuple[str, str]:
    # Only edges in TARGET_EDGES are M1 remediation targets. Other
    # version-debt edges stay classified as version_debt/deferred so
    # M1 acceptance can require version_debt_after ⊆ version_debt_before.
    if (src, dst) in TARGET_EDGES:
        if dst == VERSION_DEBT_DST:
            return "version_debt", "target"
        return "architectural", "target"
    if dst == VERSION_DEBT_DST:
        return "version_debt", "deferred"
    return "architectural", "deferred"


def _in_scope(local_mod: str, known: Set[str]) -> bool:
    if not local_mod or local_mod == VERSION_DEBT_DST:
        return local_mod == VERSION_DEBT_DST
    if local_mod in known:
        return True
    # Destination may be a package dir that has __init__.py (already in known as "").
    # Also accept if it prefixes any known module (``pipeline`` → ``pipeline.runner``).
    return any(k == local_mod or k.startswith(local_mod + ".") for k in known)


def collect_edges() -> GraphResult:
    result = GraphResult()
    adj: Dict[str, Set[str]] = defaultdict(set)
    seen: Set[Tuple[str, str, str]] = set()

    py_files = sorted(PACKAGE_ROOT.rglob("*.py"))
    module_of: Dict[Path, Tuple[List[str], bool]] = {}
    for path in py_files:
        parts = module_parts_from_path(path)
        is_pkg = path.name == "__init__.py"
        module_of[path] = (parts, is_pkg)
        result.modules.append(local_name(parts))

    known = {local_name(p) for p, _ in module_of.values()}
    known.discard("")

    def add_edge(src: str, dst: str, kind: str, detail: str) -> None:
        if not dst:
            return
        if dst != VERSION_DEBT_DST and not _in_scope(dst, known):
            return
        cls, rem = classification_for(src, dst)
        if src == dst:
            if src not in result.self_loops:
                result.self_loops.append(src)
            return
        key = (src, dst, kind)
        if key in seen:
            return
        seen.add(key)
        adj[src].add(dst)
        result.edges.append(
            Edge(src=src, dst=dst, kind=kind, classification=cls, remediation=rem, detail=detail)
        )
        if cls == "version_debt":
            result.version_debt.append(f"{src}->{dst}")

    for path in py_files:
        parts, is_pkg = module_of[path]
        src = local_name(parts)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover
            print(f"WARN: cannot parse {path}: {exc}", file=sys.stderr)
            continue

        # Parent map for delayed-import detection.
        parent: Dict[ast.AST, ast.AST] = {}
        for child in ast.walk(tree):
            for sub in ast.iter_child_nodes(child):
                parent[sub] = child

        def in_function(node: ast.AST) -> bool:
            p = parent.get(node)
            while p is not None:
                if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return True
                p = parent.get(p)
            return False

        for node in tree.body:
            # Walk top-level and nested via ast.walk, then mark delayed.
            pass

        for node in ast.walk(tree):
            delayed = in_function(node)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name.startswith(PACKAGE_NAME + "."):
                        add_edge(src, local_name(name.split(".")[1:]), "import", name)
            elif isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
                level = node.level or 0
                kind = "delayed" if delayed else "from_import"
                if level > 0:
                    dests = resolve_relative_import(parts, is_pkg, level, node.module, names)
                else:
                    dests = resolve_absolute_import(node.module or "", names)
                detail = f"from {'.' * level}{node.module or ''} import {', '.join(names)}"
                for dst in dests:
                    add_edge(src, dst, kind, detail)

    result.sccs = [c for c in tarjan_scc(adj) if len(c) > 1]
    result.sccs.sort(key=lambda c: (-len(c), sorted(c)))
    result.version_debt = sorted(set(result.version_debt))
    result.self_loops = sorted(set(result.self_loops))
    result.edges.sort(key=lambda e: (e.src, e.dst, e.kind, e.detail))
    return result


def tarjan_scc(graph: Dict[str, Set[str]]) -> List[List[str]]:
    """Iterative Tarjan SCC to avoid recursion limits on large graphs."""
    index: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    sccs: List[List[str]] = []
    counter = 0

    nodes: Set[str] = set(graph.keys())
    for targets in graph.values():
        nodes |= targets

    for start in sorted(nodes):
        if start in index:
            continue
        # Iterative Tarjan
        work: List[Tuple[str, int]] = [(start, 0)]
        while work:
            node, i = work[-1]
            if i == 0:
                index[node] = counter
                lowlink[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            recurse = False
            succ_list = sorted(graph.get(node, ()))
            if i < len(succ_list):
                work[-1] = (node, i + 1)
                succ = succ_list[i]
                if succ not in index:
                    work.append((succ, 0))
                    recurse = True
                elif succ in on_stack:
                    lowlink[node] = min(lowlink[node], index[succ])
            if recurse:
                continue
            if i >= len(succ_list):
                # Done with node
                work.pop()
                if work:
                    parent, _ = work[-1]
                    lowlink[parent] = min(lowlink[parent], lowlink[node])
                if lowlink[node] == index[node]:
                    component: List[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == node:
                            break
                    sccs.append(sorted(component))
    return sccs


def to_manifest(result: GraphResult) -> dict:
    edges_out = [
        {
            "from": e.src,
            "to": e.dst,
            "classification": e.classification,
            "remediation": e.remediation,
            "kind": e.kind,
        }
        for e in result.edges
    ]
    return {
        "scope": "production_package_only",
        "package": PACKAGE_NAME,
        "edges": edges_out,
        "baseline_scc": [sorted(c) for c in result.sccs],
        "self_loop": result.self_loops,
        "version_debt": result.version_debt,
        "approved_new_edges": sorted(
            [{"from": a, "to": b} for a, b in APPROVED_NEW_EDGES], key=lambda d: (d["from"], d["to"])
        ),
        "target_edges": sorted(
            [{"from": a, "to": b} for a, b in TARGET_EDGES], key=lambda d: (d["from"], d["to"])
        ),
    }


def emit_text(manifest: dict) -> str:
    lines: List[str] = [
        f"scope: {manifest['scope']}",
        f"package: {manifest['package']}",
        "edges:",
    ]
    for e in manifest["edges"]:
        lines.append(
            "  - {from: %s, to: %s, classification: %s, remediation: %s, kind: %s}"
            % (e["from"], e["to"], e["classification"], e["remediation"], e["kind"])
        )
    lines.append("baseline_scc:")
    for comp in manifest["baseline_scc"]:
        lines.append("  - [%s]" % ", ".join(comp))
    lines.append("self_loop:")
    for s in manifest["self_loop"]:
        lines.append(f"  - {s}")
    lines.append("version_debt:")
    for v in manifest["version_debt"]:
        lines.append(f"  - {v}")
    lines.append("approved_new_edges:")
    for e in manifest["approved_new_edges"]:
        lines.append("  - {from: %s, to: %s}" % (e["from"], e["to"]))
    lines.append("target_edges:")
    for e in manifest["target_edges"]:
        lines.append("  - {from: %s, to: %s}" % (e["from"], e["to"]))
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", metavar="PATH", help="Write baseline manifest YAML")
    parser.add_argument("--print", action="store_true", help="Print human-readable summary")
    parser.add_argument(
        "--strict-check",
        metavar="PATH",
        help="Compare current graph against baseline; exit 1 on regression",
    )
    args = parser.parse_args(argv)

    result = collect_edges()
    manifest = to_manifest(result)

    if args.print or not args.write_baseline:
        print(f"modules: {len(result.modules)}")
        print(f"edges: {len(result.edges)}")
        print(f"self_loops: {result.self_loops}")
        print(f"version_debt: {result.version_debt}")
        print(f"sccs ({len(result.sccs)}):")
        for c in result.sccs[:30]:
            print(f"  [{', '.join(c)}]")
        target_now = [e for e in result.edges if e.remediation == "target"]
        print(f"target-classified edges now: {len(target_now)}")
        for e in target_now:
            print(f"  {e.src} -> {e.dst} ({e.classification}/{e.remediation})")

    if args.write_baseline:
        path = Path(args.write_baseline)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(emit_text(manifest), encoding="utf-8")
        print(f"wrote baseline manifest: {path}")

    if args.strict_check:
        if yaml is None:
            print("ERROR: PyYAML required for --strict-check", file=sys.stderr)
            return 2
        baseline = yaml.safe_load(Path(args.strict_check).read_text(encoding="utf-8"))
        base_edges = {
            (e["from"], e["to"]): (e.get("classification"), e.get("remediation"))
            for e in baseline.get("edges", [])
        }
        cur_edges = {(e.src, e.dst): (e.classification, e.remediation) for e in result.edges}
        base_target = {k for k, v in base_edges.items() if v[1] == "target"}
        cur_target = {k for k, v in cur_edges.items() if v[1] == "target"}
        base_deferred = {k for k, v in base_edges.items() if v[1] == "deferred"}
        cur_deferred = {k for k, v in cur_edges.items() if v[1] == "deferred"}
        approved = {(e["from"], e["to"]) for e in baseline.get("approved_new_edges", [])}
        base_vd = set(baseline.get("version_debt", []))
        cur_vd = set(result.version_debt)

        failures: List[str] = []
        if cur_target:
            failures.append(f"target edges remaining: {sorted(cur_target)}")
        extra_deferred = cur_deferred - base_deferred - approved
        if extra_deferred:
            failures.append(f"new deferred edges not in baseline: {sorted(extra_deferred)}")
        extra_vd = cur_vd - base_vd
        if extra_vd:
            failures.append(f"new version_debt: {sorted(extra_vd)}")
        unclassified = [e for e in result.edges if not e.classification or not e.remediation]
        if unclassified:
            failures.append(f"unclassified edges: {len(unclassified)}")

        if failures:
            print("STRICT CHECK FAILED:", file=sys.stderr)
            for item in failures:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print("strict check OK")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
