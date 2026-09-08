# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gate: every string-literal metadata key used across ``src`` must be
declared in ``MetadataDict``.

Scan **all** ``src/movie_narrator/**/*.py`` for literal metadata access
patterns (bounded to the variable name ``metadata``), collect the distinct
string-literal keys used, and fail unless every one is also declared as a
key in ``MetadataDict`` (``src/movie_narrator/models.py``).

Dynamic keys (variable-driven) are intentionally not matched — only string
literals. Run directly, no third-party dependencies required::

    python scripts/check_metadata_keys.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "src" / "movie_narrator" / "models.py"
SRC = ROOT / "src" / "movie_narrator"
KEY_DECL_RE = re.compile(r"^\s+(?P<key>[A-Za-z_][A-Za-z0-9_]*):\s+", re.MULTILINE)
# Literal key access on ``metadata``:
#   metadata.get("KEY") / metadata["KEY"] / metadata.pop("KEY") / metadata.setdefault("KEY")
#   metadata.get("KEY", default) / .pop("KEY", default)   (the opener already matches)
#   metadata["KEY"].method(...)                            (e.g. [...].append(...))
#   metadata.update({"KEY": ...})
# NOTE on `\bmetadata`: this matches *any* variable named ``metadata``, which
# includes ``task.metadata["..."]`` attribute access on the Task model. Those
# Task keys are therefore enforced against the same `MetadataDict` declaration
# as the Context `metadata`. This is stricter than strictly necessary (a Task
# key missing from MetadataDict is reported and must be declared), but the
# Task/Context keys are presently a subset, so the conflation is harmless and
# acts as a single, conservative gate. A future stricter variant could narrow
# by object type; today the trade-off is deliberate and covered by this docstring.
METADATA_LITERAL_RE = re.compile(
    r"\bmetadata\s*(?:\.(?:get|pop|setdefault)\(\s*|\[|\.update\(\s*\{\s*)"
    r'(?P<quote>["\'])(?P<key>[A-Za-z0-9_.-]+)(?P=quote)',
)


def declared_keys(text: str) -> set[str]:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"\s*class\s+MetadataDict\s*\(", line):
            start = i
            break
    if start is None:
        raise SystemExit("Error: class MetadataDict not found in models.py")

    # Collect the indented class body: covers the module docstring and every
    # ``key: type`` line. Stop at the first top-level (non-indented, non-blank,
    # non-comment) construct after the class — i.e. the next ``class``/``if``.
    body_lines = []
    for line in lines[start + 1 :]:
        if line and not line.startswith((" ", "\t")):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                break
            continue
        body_lines.append(line)

    body = "\n".join(body_lines)
    return set(KEY_DECL_RE.findall(body))


def literal_keys_in_text(text: str) -> set[str]:
    return set(m["key"] for m in METADATA_LITERAL_RE.finditer(text))


def main() -> int:
    models_text = MODELS.read_text(encoding="utf-8")
    declared = declared_keys(models_text)

    used: set[str] = set()
    for py in SRC.rglob("*.py"):
        used |= literal_keys_in_text(py.read_text(encoding="utf-8"))

    missing = sorted(u for u in used if u not in declared)
    if missing:
        print("Undocumented metadata keys:")
        for key in missing:
            print(f"  {key}")
        print("Add them to MetadataDict in src/movie_narrator/models.py.")
        return 1

    print(f"OK: {len(declared)} keys, all declared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
