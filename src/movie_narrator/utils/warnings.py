"""Shared warning accumulation utility.

Both ``translate_subtitles`` and ``export_clips`` need to append
warnings to ``ctx.metadata["warnings"]``.  This module provides a
single implementation so the list-initialization logic is not
duplicated.
"""

from __future__ import annotations

from ..models import Context


def append_warning(ctx: Context, msg: str, *, prefix: str | None = None) -> None:
    """Append *msg* to ``ctx.metadata["warnings"]``.

    Creates the list if missing.  If the existing value is not a list
    (defensive), it is coerced to one.

    Parameters
    ----------
    ctx:
        Pipeline context.
    msg:
        Warning message.
    prefix:
        Optional step-name prefix, e.g. ``"export_clips"``.
        When provided, the stored message becomes ``"{prefix}: {msg}"``.
    """
    warnings = ctx.metadata.setdefault("warnings", [])
    if not isinstance(warnings, list):
        warnings = list(warnings)
        ctx.metadata["warnings"] = warnings
    if prefix:
        warnings.append(f"{prefix}: {msg}")
    else:
        warnings.append(msg)
