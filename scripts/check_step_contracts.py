# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gate: validate M4 step contracts on the canonical step registry.

Thin wrapper around
:func:`movie_narrator.pipeline.step_contracts.validate_step_contracts`.
Exits 0 when every registered step passes; prints errors and exits 1
otherwise. The pytest module ``tests/test_m4_step_contracts.py`` is the
primary gate; this script is an optional CI/console entry point::

    python scripts/check_step_contracts.py
"""

from __future__ import annotations

import sys


def main() -> int:
    # Importing the runner registers the 16 built-in steps.
    from movie_narrator.pipeline import runner  # noqa: F401
    from movie_narrator.pipeline.step_contracts import validate_step_contracts

    errors = validate_step_contracts()
    if errors:
        print(f"Step contract gate failed ({len(errors)} error(s)):")
        for err in errors:
            print(f"  {err}")
        return 1
    print("OK: all registered steps pass the M4 step-contract gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
