# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Environment pre-flight diagnostics for ``mn doctor``.

Checks the runtime prerequisites a first-time user needs before running the
pipeline: a usable ffmpeg binary, the optional feature extras (``[media]``,
``[ml]``), and the presence of infra credentials in the settings layer.

All checks are read-only and never raise — they produce a structured list of
``Diagnostic`` entries (``name`` / ``ok`` / ``detail`` / ``hint``) plus a
green/yellow/red summary so the CLI can render a simple table. This keeps the
command side-effect free and trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .utils.ffmpeg_bin import ffmpeg_bin
from .utils.optional_deps import DepStatus, probe_status

# Optional dependency probes: (logical name, probe-supported key).
# The probe key is one of the keys understood by ``optional_deps.probe``.
_OPTIONAL_DEPS: List[Tuple[str, str]] = [
    ("scenedetect  ([media])", "scenedetect"),
    ("whisperx       ([ml])", "whisperx"),
    ("faster-whisper ([ml])", "faster_whisper"),
    ("funasr         ([ml])", "funasr"),
    ("sentence-transformers ([ml])", "sentence_transformers"),
]


@dataclass
class Diagnostic:
    """A single named check result."""

    name: str
    ok: bool
    detail: str = ""
    hint: str = ""

    def to_row(self) -> Tuple[str, str, str]:
        """Return (name, status, message) for table rendering."""
        status = "OK" if self.ok else "MISSING"
        message = self.detail or self.hint
        return (self.name, status, message)


@dataclass
class DoctorReport:
    """Aggregated result of a ``mn doctor`` run."""

    checks: List[Diagnostic] = field(default_factory=list)

    def add(self, diagnostic: Diagnostic) -> None:
        self.checks.append(diagnostic)

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.checks if c.ok)

    @property
    def missing(self) -> List[Diagnostic]:
        return [c for c in self.checks if not c.ok]

    @property
    def healthy(self) -> bool:
        """True when every check passed (no missing prerequisites)."""
        return not self.missing


def _ffmpeg_check() -> Diagnostic:
    """Check for a usable ffmpeg binary (system or imageio fallback)."""
    try:
        path = ffmpeg_bin()
    except Exception as exc:  # noqa: BLE001
        return Diagnostic(
            name="ffmpeg",
            ok=False,
            detail=f"lookup failed: {exc}",
            hint="install FFmpeg and add it to PATH, or pip install imageio-ffmpeg",
        )
    if not path or path == "ffmpeg":  # bare fallback name — probably not installed
        return Diagnostic(
            name="ffmpeg",
            ok=False,
            detail="not found on PATH",
            hint="install FFmpeg and add it to PATH (or pip install imageio-ffmpeg)",
        )
    return Diagnostic(name="ffmpeg", ok=True, detail=path)


def _optional_deps_checks() -> List[Diagnostic]:
    """Probe the optional feature extras and return their diagnostics.

    Distinguishes ``OK`` (usable), ``NOT_INSTALLED`` (module absent) and
    ``MISSING_DEPS`` (installed but a transitive dependency is broken) so the
    report surfaces the precise remediation.
    """
    results: List[Diagnostic] = []
    for label, key in _OPTIONAL_DEPS:
        try:
            status, hint = probe_status(key)
        except Exception as exc:  # noqa: BLE001
            results.append(
                Diagnostic(name=label, ok=False, detail=f"probe error: {exc}", hint="")
            )
            continue
        if status is DepStatus.OK:
            results.append(Diagnostic(name=label, ok=True, detail="installed"))
        elif status is DepStatus.NOT_INSTALLED:
            results.append(Diagnostic(name=label, ok=False, detail="not installed", hint=hint))
        else:  # DepStatus.MISSING_DEPS
            results.append(Diagnostic(name=label, ok=False, detail="missing deps", hint=hint))
    return results


def _config_checks() -> List[Diagnostic]:
    """Check that infra credentials are plausibly configured.

    Reads the settings layer (``.env`` / ``~/.movie-narrator/.env``) and the
    user config file existence. These are advisory — a fresh install has
    working built-in defaults (local Ollama), so a missing key is a warning
    rather than a hard error.
    """
    from .config import _USER_ENV, get_settings

    checks: List[Diagnostic] = []

    env_path: Optional[Path] = None
    try:
        env_path = _USER_ENV
        exists = env_path is not None and env_path.exists()
    except Exception:  # noqa: BLE001
        exists = False
    checks.append(
        Diagnostic(
            name="user .env",
            ok=bool(exists),
            detail=str(env_path) if env_path else "",
            hint="create ~/.movie-narrator/.env (auto-created on first run)",
        )
    )

    try:
        settings = get_settings()
        llm_configured = bool(settings.llm_base_url and settings.llm_model)
        checks.append(
            Diagnostic(
                name="LLM config",
                ok=llm_configured,
                detail=f"provider={settings.llm_provider} model={settings.llm_model}",
                hint="set MN_LLM_BASE_URL / MN_LLM_MODEL in ~/.movie-narrator/.env",
            )
        )
        tts_configured = bool(settings.default_voice)
        checks.append(
            Diagnostic(
                name="TTS config",
                ok=tts_configured,
                detail=f"provider={settings.tts_provider.value} voice={settings.default_voice}",
                hint="set MN_DEFAULT_VOICE in ~/.movie-narrator/.env",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            Diagnostic(name="settings", ok=False, detail=f"load error: {exc}", hint="check .env syntax")
        )
    return checks


def run_doctor() -> DoctorReport:
    """Run all pre-flight diagnostics and return the report."""
    report = DoctorReport()
    report.add(_ffmpeg_check())
    for d in _optional_deps_checks():
        report.add(d)
    for d in _config_checks():
        report.add(d)
    return report


def render_report(report: DoctorReport) -> str:
    """Render a doctor report as a simple aligned table."""
    lines: List[str] = []
    lines.append(f"{'Check':<28} {'Status':<9} Detail")
    lines.append("-" * 72)
    for check in report.checks:
        status = "OK" if check.ok else "MISSING"
        message = check.detail or ""
        if check.hint and not check.ok:
            message = f"{message}  ({check.hint})" if message else check.hint
        lines.append(f"{check.name:<28} {status:<9} {message}")
    lines.append("")
    lines.append(
        f"Summary: {report.ok_count}/{len(report.checks)} checks passed, "
        f"{len(report.missing)} missing."
    )
    if report.healthy:
        lines.append("Environment looks ready to run the pipeline.")
    else:
        lines.append("Run `mn create` anyway — pipeline soft-degrades on missing extras.")
    return "\n".join(lines)
