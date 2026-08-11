# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

#!/usr/bin/env python3
"""ADR-011 forbidden-dependency guard for CI.

Fails if any distribution from the ADR-011 licensing red-line list is
installed in the current environment. Runs against the installed
environment (importlib.metadata), so no extra dependency is required.

See docs/ADR.md ADR-011 for the full rationale.
"""

from __future__ import annotations

import sys
from importlib.metadata import distributions

# Canonical distribution names (PEP 503 normalized) prohibited by ADR-011.
# Keep this list in sync with the "License Red Lines" section in
# docs/CONTRIBUTING.md and docs/ADR.md ADR-011.
FORBIDDEN: dict[str, str] = {
    "remotion": "Remotion custom license",
    "typetale": "TypeTale source code",
    "yt-dlp": "material crawling scraper",
    "bilibili-api": "material crawling scraper",
    "playwright": "streaming-platform scraping",
    "indextts": "voice cloning",
    "index-tts": "voice cloning",
    "cosyvoice": "voice cloning",
}


def normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def main() -> int:
    hits: list[tuple[str, str]] = []
    for dist in distributions():
        raw = dist.metadata.get("Name")
        if not raw:
            continue
        norm = normalize(raw)
        if norm in FORBIDDEN:
            hits.append((raw, FORBIDDEN[norm]))

    if hits:
        print("ERROR: ADR-011 forbidden dependencies detected:")
        for name, reason in sorted(hits):
            print(f"  - {name}: {reason}")
        return 1

    print("ADR-011 forbidden-dependency guard: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
