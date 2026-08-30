# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Plans & entitlements — quota/policy enforcement for task submissions (v1.3.1).

A :class:`Plan` is a frozen set of product limits (duration, resolution,
artifact size, watermark policy, GPU-encoder permission, artifact TTL).
Plans turn the engine into a *service*: an operator can cap what a
submission may request without touching pipeline code.

Built-in plans:

- ``default`` — everything unlimited; watermark off; GPU allowed. This is
  the only plan used unless ``MN_DEFAULT_PLAN`` / ``X-MN-Plan`` say
  otherwise, so **the pre-v1.3.1 behaviour is unchanged by default**.
- ``free`` — 720p max, 60 s max, 512 MiB estimated artifacts, mandatory
  watermark, CPU-only encoding, 24 h artifact TTL.
- ``pro`` — 1080p max, 3600 s max, 8 GiB estimated artifacts, no
  watermark, GPU allowed, 72 h artifact TTL.

Custom plans may be supplied via ``MN_PLANS_FILE`` pointing at a JSON
file of the shape ``{"plans": [{"name": ..., ...limit fields...}]}``.
Entries with a built-in name override the built-in; parsing is tolerant —
an unreadable/invalid file logs a warning and the built-ins remain active.

Enforcement points (v1.3.1):

1. **Submission** — ``cloud/api.py`` validates the incoming request via
   :func:`check_submission` and answers ``403`` with an
   ``{"error": "entitlement_denied", ...}`` body on violation.
2. **Worker** — ``cloud/worker.py`` injects plan policy into the job
   params/context before running the pipeline (mandatory watermark, forced
   CPU encoder). Pipeline code is untouched.

Environment variables:
    ``MN_DEFAULT_PLAN``  plan used when the request does not pick one
                         (empty/unset = ``default`` = all limits off)
    ``MN_PLANS_FILE``    optional JSON file with plan overrides
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .models import TaskRequest

logger = logging.getLogger(__name__)

#: Environment variable holding the plan used when a request does not
#: choose one. Empty/unset means the unlimited ``default`` plan.
ENV_DEFAULT_PLAN = "MN_DEFAULT_PLAN"

#: Environment variable pointing at an optional JSON plan-overrides file.
ENV_PLANS_FILE = "MN_PLANS_FILE"

#: Watermark text injected when a plan mandates a watermark and none is
#: configured. Follows the preset ``render_template.watermark_text``
#: convention: the ``{movie}`` placeholder is substituted with the movie
#: name at render time. Distinct from the preset watermarks (e.g.
#: ``douyin-fast``) so a plan-injected mark is always recognizable.
PLAN_WATERMARK_TEXT = "{movie} · mn"

#: Header used by API callers to pick a plan for a single request.
PLAN_HEADER = "X-MN-Plan"


# ── Plan model ─────────────────────────────────────────────


@dataclass(frozen=True)
class Plan:
    """Immutable product limits for task submissions.

    Attributes:
        name: Plan identifier (``"default"``, ``"free"``, ``"pro"``, ...).
        max_duration_s: Maximum requested narration duration in seconds.
            ``None`` = unlimited.
        max_resolution: Maximum render resolution as ``(width, height)``.
            Compared orientation-normalized (both sides sorted so the
            larger dimension comes first), so a portrait ``9:16`` request
            is judged on the same scale as ``16:9``. ``None`` = unlimited.
        max_artifact_bytes: Cap on the estimated output size in bytes.
            ``None`` = unlimited.
        watermark_required: When True the worker injects a watermark via
            the ``render_template.watermark_text`` param if none is
            configured (request params or narration preset).
        allow_gpu_encoder: When False the worker forces the ``cpu`` encoder
            hint, so no GPU encode is attempted (and no runtime GPU→CPU
            fallback observability event fires — policy, not failure).
        artifact_ttl_hours: Retention hint for the task's artifacts, in
            hours. ``None`` = no plan-driven TTL (the generic
            ``MN_ARTIFACT_*`` lifecycle policy still applies).
    """

    name: str
    max_duration_s: Optional[float] = None
    max_resolution: Optional[Tuple[int, int]] = None
    max_artifact_bytes: Optional[int] = None
    watermark_required: bool = False
    allow_gpu_encoder: bool = True
    artifact_ttl_hours: Optional[float] = None


#: Unlimited plan — the only default. Zero behaviour change vs v1.2.
DEFAULT = Plan(name="default")

#: Free tier: 720p / 60 s / 512 MiB / watermark / CPU-only / 24 h TTL.
FREE = Plan(
    name="free",
    max_duration_s=60.0,
    max_resolution=(1280, 720),
    max_artifact_bytes=512 * 1024 * 1024,
    watermark_required=True,
    allow_gpu_encoder=False,
    artifact_ttl_hours=24.0,
)

#: Pro tier: 1080p / 3600 s / 8 GiB / no watermark / GPU / 72 h TTL.
PRO = Plan(
    name="pro",
    max_duration_s=3600.0,
    max_resolution=(1920, 1080),
    max_artifact_bytes=8 * 1024 * 1024 * 1024,
    watermark_required=False,
    allow_gpu_encoder=True,
    artifact_ttl_hours=72.0,
)

_BUILTIN_PLANS: Dict[str, Plan] = {
    p.name: p for p in (DEFAULT, FREE, PRO)
}


# ── Override file loading (tolerant) ───────────────────────


def _parse_plan(raw: Any) -> Optional[Plan]:
    """Parse one JSON object into a :class:`Plan` (tolerant).

    Returns None (and logs) for entries that cannot be used; unknown keys
    and absent optional fields are ignored.
    """
    if not isinstance(raw, dict):
        logger.warning("MN_PLANS_FILE: ignoring non-object plan entry")
        return None
    name = str(raw.get("name", "")).strip().lower()
    if not name:
        logger.warning("MN_PLANS_FILE: ignoring plan entry without a name")
        return None
    try:
        max_duration = raw.get("max_duration_s")
        max_res = raw.get("max_resolution")
        max_bytes = raw.get("max_artifact_bytes")
        ttl = raw.get("artifact_ttl_hours")
        return Plan(
            name=name,
            max_duration_s=(None if max_duration is None else float(max_duration)),
            max_resolution=(
                None
                if max_res is None
                else (int(max_res[0]), int(max_res[1]))
            ),
            max_artifact_bytes=(None if max_bytes is None else int(max_bytes)),
            watermark_required=bool(raw.get("watermark_required", False)),
            allow_gpu_encoder=bool(raw.get("allow_gpu_encoder", True)),
            artifact_ttl_hours=(None if ttl is None else float(ttl)),
        )
    except (TypeError, ValueError, IndexError, KeyError) as e:
        logger.warning(
            "MN_PLANS_FILE: ignoring malformed plan entry %r: %s", raw.get("name"), e
        )
        return None


def _load_plan_overrides(path: Path) -> Dict[str, Plan]:
    """Load plan overrides from a JSON file (never raises).

    The result is keyed by plan name; invalid files yield an empty mapping
    so the built-ins remain active.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("MN_PLANS_FILE unreadable (%s): %s — using built-ins", path, e)
        return {}
    entries = data.get("plans") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        logger.warning(
            "MN_PLANS_FILE malformed (expected {\"plans\": [...]}): %s — using built-ins",
            path,
        )
        return {}
    plans: Dict[str, Plan] = {}
    for raw in entries:
        plan = _parse_plan(raw)
        if plan is not None:
            plans[plan.name] = plan
    return plans


def _plan_registry() -> Dict[str, Plan]:
    """Built-ins plus any valid overrides from ``MN_PLANS_FILE``."""
    registry = dict(_BUILTIN_PLANS)
    raw_path = os.environ.get(ENV_PLANS_FILE, "").strip()
    if not raw_path:
        return registry
    overrides = _load_plan_overrides(Path(raw_path))
    registry.update(overrides)
    return registry


# ── Resolution ─────────────────────────────────────────────


def available_plan_names() -> Tuple[str, ...]:
    """Names of all configured plans (built-ins + overrides), sorted."""
    return tuple(sorted(_plan_registry()))


def resolve_plan(name: str) -> Plan:
    """Resolve a plan by name.

    Args:
        name: Plan name (case-insensitive). ``""`` resolves to
            :data:`DEFAULT`.

    Returns:
        The configured :class:`Plan`.

    Raises:
        KeyError: when *name* does not match any configured plan.
    """
    key = (name or "").strip().lower()
    if not key:
        return DEFAULT
    registry = _plan_registry()
    if key in registry:
        return registry[key]
    raise KeyError(f"unknown plan: {name!r}")


def default_plan_name(env: Optional[Mapping[str, str]] = None) -> str:
    """Name of the plan used when a request does not pick one (tolerant).

    Reads ``MN_DEFAULT_PLAN``; an unset/empty value resolves to
    ``"default"``. An unknown configured name logs a warning and falls
    back to ``"default"`` so a typo can never lock operators out.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    raw = (environ.get(ENV_DEFAULT_PLAN) or "").strip()
    if not raw:
        return DEFAULT.name
    try:
        resolve_plan(raw)
    except KeyError:
        logger.warning(
            "Ignoring unknown %s=%r — falling back to the unlimited default plan",
            ENV_DEFAULT_PLAN,
            raw,
        )
        return DEFAULT.name
    return raw.lower()


# ── Submission entitlement check ───────────────────────────


class EntitlementError(Exception):
    """A submission violates its plan's limits (v1.3.1).

    Machine-readable fields for the API layer's 403 body:

    Attributes:
        plan: Name of the enforcing plan.
        limit: The limit identifier that was hit
            (``max_duration_s`` / ``max_resolution`` / ``max_artifact_bytes``).
        actual: The offending requested value.
    """

    def __init__(self, plan: str, limit: str, actual: Any) -> None:
        self.plan = plan
        self.limit = limit
        self.actual = actual
        super().__init__(
            f"plan '{plan}' denies {limit}: requested {actual!r}"
        )


def _normalized(size: Tuple[int, int]) -> Tuple[int, int]:
    """Orientation-normalize a ``(width, height)`` tuple (larger first)."""
    return tuple(sorted((int(size[0]), int(size[1])), reverse=True))  # type: ignore[return-value]


def check_submission(
    plan: Plan,
    *,
    duration_s: Optional[float] = None,
    resolution: Optional[Tuple[int, int]] = None,
    estimated_bytes: Optional[int] = None,
) -> None:
    """Validate a submission against *plan*; raise :class:`EntitlementError`.

    Args:
        plan: The enforcing plan.
        duration_s: Requested narration duration in seconds. ``None``
            skips the duration check.
        resolution: Requested render resolution ``(width, height)``.
            Compared orientation-normalized. ``None`` skips the check.
        estimated_bytes: Estimated output size in bytes. ``None`` skips
            the check.

    Raises:
        EntitlementError: on the first violated limit.
    """
    if plan.max_duration_s is not None and duration_s is not None:
        if duration_s > plan.max_duration_s:
            raise EntitlementError(plan.name, "max_duration_s", duration_s)
    if plan.max_resolution is not None and resolution is not None:
        actual = _normalized(resolution)
        limit = _normalized(plan.max_resolution)
        if actual[0] > limit[0] or actual[1] > limit[1]:
            raise EntitlementError(plan.name, "max_resolution", resolution)
    if plan.max_artifact_bytes is not None and estimated_bytes is not None:
        if estimated_bytes > plan.max_artifact_bytes:
            raise EntitlementError(plan.name, "max_artifact_bytes", estimated_bytes)


# ── Submission-side resolution helpers ─────────────────────


#: Default render sizes per ``video_format`` — mirrors the pipeline's
#: ``video_sizes`` default (``pipeline/render.py``) so entitlement checks
#: judge what the renderer would actually produce.
_DEFAULT_VIDEO_SIZES: Dict[str, Tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
}


def submission_resolution(request: TaskRequest) -> Tuple[int, int]:
    """The render resolution a submission would produce.

    Uses ``params.video_sizes[<video_format>]`` when supplied (the same
    knob the renderer consumes), otherwise the built-in default for the
    request's ``video_format``.
    """
    sizes = (request.params or {}).get("video_sizes")
    if isinstance(sizes, dict):
        entry = sizes.get(request.video_format)
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            try:
                return (int(entry[0]), int(entry[1]))
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring malformed video_sizes entry %r for entitlement check",
                    entry,
                )
    return _DEFAULT_VIDEO_SIZES.get(request.video_format, (1920, 1080))


__all__ = [
    "DEFAULT",
    "FREE",
    "PRO",
    "ENV_DEFAULT_PLAN",
    "ENV_PLANS_FILE",
    "PLAN_HEADER",
    "PLAN_WATERMARK_TEXT",
    "EntitlementError",
    "Plan",
    "available_plan_names",
    "check_submission",
    "default_plan_name",
    "resolve_plan",
    "submission_resolution",
]
