# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Enforce a per-file line-count budget against mega-files.

Rules:
- Any *newly added* source file (added on this branch vs a base ref) that
  exceeds ``--max-lines`` is a hard failure (exit 1).
- Existing files already over ``--max-lines`` are reported as warnings and do
  not fail the build (they are tracked separately so a future split can be
  verified without breaking CI).

Usage:
    python scripts/lint_file_size.py                          # warn-only report
    python scripts/lint_file_size.py --added-vs origin/main   # fail on new offenders

Requires: stdlib only.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "movie_narrator"
DEFAULT_MAX = 900


def _count(path: Path) -> int:
    return sum(1 for _ in path.open(encoding="utf-8"))


def _added_files(base: str) -> set[Path]:
    cmd = ["git", "diff", "--name-only", "--diff-filter=A", f"{base}...HEAD", "--"]
    try:
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    return {ROOT / line for line in out.splitlines() if line}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-lines", type=int, default=DEFAULT_MAX)
    ap.add_argument("--added-vs", default=None, help="base ref to compare added files")
    args = ap.parse_args()

    py_files = sorted(SRC.rglob("*.py"))
    added = _added_files(args.added_vs) if args.added_vs else set()

    over = []
    for p in py_files:
        n = _count(p)
        if n > args.max_lines:
            over.append((p.relative_to(ROOT), n))

    over_new = [(rel, n) for rel, n in over if (ROOT / rel) in added]
    fail = 0
    if over_new:
        fail = 1
        for rel, n in sorted(over_new):
            print(f"FAIL  NEW src file over budget: {rel} ({n} lines > {args.max_lines})")
    if over:
        for rel, n in sorted(over):
            mark = "NEW " if (ROOT / rel) in added else "EXIST"
            print(f"INFO  {mark} over budget: {rel} ({n} lines)")

    if fail:
        return 1
    if not over:
        print(f"OK: no file exceeds {args.max_lines} lines.")
    else:
        print("WARN: existing over-budget files detected (non-fatal).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
