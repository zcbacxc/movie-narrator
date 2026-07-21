from importlib import import_module
from typing import Tuple

_HINTS = {
    "scenedetect": 'pip install "movie-narrator[media]"',
    "whisperx": 'pip install "movie-narrator[ml]"',
    "faster_whisper": 'pip install "movie-narrator[ml]"',
    "sentence_transformers": 'pip install "movie-narrator[ml]"',
}


def probe(name: str) -> Tuple[bool, str]:
    """Return (available, install_hint)."""
    module_names = {
        "scenedetect": "scenedetect",
        "whisperx": "whisperx",
        "faster_whisper": "faster_whisper",
        "sentence_transformers": "sentence_transformers",
    }
    mod = module_names.get(name, name)
    try:
        import_module(mod)
        return True, ""
    except Exception:
        return False, _HINTS.get(name, f"install dependency for {name}")
