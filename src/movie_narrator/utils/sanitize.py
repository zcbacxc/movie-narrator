"""Shared filename sanitization utility.

Used by both CLI and Web UI to ensure consistent directory/file naming
across the application. Single source of truth — both callers import
from here.
"""

import re

_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a directory or file name.

    - Replaces invalid characters (< > : " / \\ | ? *) with underscore
    - Strips leading/trailing whitespace and trailing dots
    - Falls back to "movie" if the result is empty
    - Prefixes Windows reserved names (CON, PRN, AUX, ...) with underscore
    """
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.strip().rstrip(".")
    if not name:
        name = "movie"
    if name.upper() in _RESERVED_NAMES:
        name = f"_{name}"
    return name
