# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Miscellaneous CLI commands: ``version``, ``doctor``, ``plugin``, ``benchmark``.

The encoder-benchmark loader helpers live here but are deliberately
re-exported from ``movie_narrator.cli`` (the package scope) because tests
monkeypatch ``movie_narrator.cli._benchmark_script_path`` and read
``movie_narrator.cli._BENCHMARK_MOD_NAME``.  The loader therefore resolves
those names through the ``movie_narrator.cli`` module object at call time.
"""

from pathlib import Path
from typing import Optional

import typer

import movie_narrator.cli as _cli

from movie_narrator import __version__


def plugin(
    action: str = typer.Argument(..., help="list | discover | registries | version"),
):
    """Plugin system commands — list, discover, inspect registries.


    Examples:
            mn plugin list          # list installed entry_points plugins
            mn plugin discover      # discover and load all plugins
            mn plugin registries    # show all registered providers/steps
            mn plugin version       # show CONTRACT_VERSION
    """
    if action == "list":
        from movie_narrator.plugin_loader import list_available_plugins

        plugins = list_available_plugins()
        if not plugins:
            typer.echo("No plugins found via entry_points.")
            typer.echo("")
            typer.echo("Plugins are discovered via the 'movie_narrator.plugins'")
            typer.echo("entry point group. Install a plugin package to see it here.")
            return
        typer.echo(f"Available plugins ({len(plugins)}):")
        for name in sorted(plugins):
            typer.echo(f"  {name}")

    elif action == "discover":
        from movie_narrator.plugin_loader import discover_plugins

        results = discover_plugins()
        if not results:
            typer.echo("No plugins found to discover.")
            return
        succeeded = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        typer.echo(f"Discovery complete: {len(succeeded)} succeeded, {len(failed)} failed")
        for r in succeeded:
            typer.echo(f"  [OK] {r.name}")
        for r in failed:
            typer.echo(f"  [FAIL] {r.name}: {r.error}", err=True)

    elif action == "registries":
        # Import factory modules to ensure built-in providers are registered
        import movie_narrator.tts.factory  # noqa: F401
        import movie_narrator.vision.factory  # noqa: F401
        import movie_narrator.utils.llm  # noqa: F401
        import movie_narrator.pipeline.research  # noqa: F401

        from movie_narrator.pipeline.registry import step_registry
        from movie_narrator.providers import (
            tts_registry,
            vision_registry,
            llm_registry,
            research_registry,
        )

        typer.echo("=== Step Registry ===")
        for info in step_registry.info():
            soft_tag = " (soft)" if info["soft"] else ""
            after_tag = f" after={info['insert_after']}" if info["insert_after"] else ""
            before_tag = f" before={info['insert_before']}" if info["insert_before"] else ""
            typer.echo(f"  {info['name']:<25}{soft_tag}{after_tag}{before_tag}")

        typer.echo("")
        typer.echo("=== TTS Registry ===")
        for info in tts_registry.info():
            proto = " [protocol]" if info["protocol_validated"] else ""
            typer.echo(f"  {info['name']:<25}{proto}")

        typer.echo("")
        typer.echo("=== Vision Registry ===")
        for info in vision_registry.info():
            proto = " [protocol]" if info["protocol_validated"] else ""
            typer.echo(f"  {info['name']:<25}{proto}")

        typer.echo("")
        typer.echo("=== LLM Registry ===")
        for info in llm_registry.info():
            proto = " [protocol]" if info["protocol_validated"] else ""
            typer.echo(f"  {info['name']:<25}{proto}")

        typer.echo("")
        typer.echo("=== Research Registry ===")
        for info in research_registry.info():
            proto = " [protocol]" if info["protocol_validated"] else ""
            typer.echo(f"  {info['name']:<25}{proto}")

    elif action == "version":
        from movie_narrator.contract import CONTRACT_VERSION

        typer.echo(f"CONTRACT_VERSION = {CONTRACT_VERSION}")
        typer.echo(f"  semver: {'.'.join(str(v) for v in CONTRACT_VERSION)}")

    else:
        raise typer.BadParameter(
            f"Unknown action: {action!r}. Use: list | discover | registries | version",
            param_hint="action",
        )


def version():
    """Show version."""
    typer.echo(f"movie-narrator v{__version__}")


def doctor():
    """Environment pre-flight check — ffmpeg, extras, config."""
    from movie_narrator.doctor import run_doctor, render_report

    report = run_doctor()
    typer.echo(render_report(report))
    if not report.healthy:
        raise typer.Exit(code=1)


# ── Encoder benchmark (v1.4.2) ────────────────────────────

#: sys.modules key the benchmark module is cached under (loaded once).
_BENCHMARK_MOD_NAME = "mn_encoder_benchmark"


def _benchmark_script_path() -> Path:
    """Location of the v1.3.2 benchmark script (source checkout layout)."""
    return Path(__file__).resolve().parents[4] / "benchmarks" / "encoder_benchmark.py"


def _load_encoder_benchmark():
    """Import ``benchmarks/encoder_benchmark.py`` by file path.

    Decision (v1.4.2): the repo ships ``benchmarks/`` as a plain script
    directory (no ``__init__.py``), so the module is loaded via
    ``importlib.util.spec_from_file_location`` instead of repackaging it
    into the package. The module is import-safe (no ffmpeg at import
    time) and cached in ``sys.modules`` so repeated calls — and tests —
    share one module object. Requires a source checkout; a wheel install
    does not carry ``benchmarks/``.
    """
    import importlib.util
    import sys

    cached = sys.modules.get(_cli._BENCHMARK_MOD_NAME)
    if cached is not None:
        return cached
    path = _cli._benchmark_script_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"benchmarks/encoder_benchmark.py not found (looked at {path}). "
            "'mn benchmark' requires a source checkout of movie-narrator."
        )
    spec = importlib.util.spec_from_file_location(_cli._BENCHMARK_MOD_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load benchmark module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_cli._BENCHMARK_MOD_NAME] = module
    spec.loader.exec_module(module)
    return module


def benchmark(
    duration: int = typer.Option(
        5,
        "--duration",
        min=1,
        help="合成测试片段时长（秒） / Synthetic clip duration in seconds (default: 5)",
    ),
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        help="JSON 报告输出路径 / Optional path for the JSON report",
    ),
    encoders: Optional[str] = typer.Option(
        None,
        "--encoders",
        help=(
            "逗号分隔的编码器过滤（label 或 codec 名，如 'libx264,nvenc'）；"
            "缺省自动检测 / Comma-separated encoder filter (label or codec "
            "name, e.g. 'libx264,nvenc'); default: auto-detect"
        ),
    ),
):
    """Benchmark ffmpeg encoders (libx264 baseline vs detected GPU encoders).

    Thin wrapper over ``benchmarks/encoder_benchmark.py`` (v1.3.2) — same
    report, same table, same JSON schema. Generates a short synthetic
    clip and encodes it once per encoder; the moviepy render path is not
    involved.

    Examples:
            mn benchmark
            mn benchmark --duration 8 --out gpu.json
            mn benchmark --encoders libx264,nvenc
    """
    try:
        bench = _load_encoder_benchmark()
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    encoders_filter = (
        [e.strip() for e in encoders.split(",") if e.strip()] if encoders else None
    )
    # Delegate to the script's own argparse main so behavior (table,
    # report writing, exit code) is identical to running the script.
    cmd = ["--duration", str(duration)]
    if out:
        cmd += ["--out", str(out)]
    if encoders_filter:
        cmd += ["--encoders", ",".join(encoders_filter)]
    raise typer.Exit(code=bench.main(cmd))
