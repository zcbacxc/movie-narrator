"""TTSCacheKey dataclass and cache filesystem layout helper."""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

CACHE_SCHEMA_VERSION = 2  # v0.4: key shape gains provider + provider_version + model

PROVIDER_CACHE_VERSIONS: dict[str, int] = {
    "edge": 1,
    "openai": 1,
    "mimo": 1,
}


@dataclass(frozen=True, slots=True)
class TTSCacheKey:
    schema_version: int   # currently 2; bumps when key shape changes
    provider: str         # "edge" | "openai"
    provider_version: int  # per-backend encoding version
    model: str            # "" for Edge, "tts-1" for OpenAI
    voice: str
    text: str
    pause_ms: int


def cache_path_for(root: Path, key: TTSCacheKey) -> Path:
    """Produce ``root/<hash[:2]>/<hash[2:4]>/<hash>.mp3`` for a cache key.

    Two-level fan-out keeps filesystem scans cheap when the cache grows.
    """
    raw = json.dumps(asdict(key), sort_keys=True, ensure_ascii=False).encode()
    h = hashlib.sha256(raw).hexdigest()
    return root / h[:2] / h[2:4] / f"{h}.mp3"
