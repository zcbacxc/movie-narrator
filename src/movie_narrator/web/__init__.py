"""Movie Narrator Web UI (Gradio-based local browser interface).

Requires the ``web`` extra: ``pip install "movie-narrator[web]"``.

``launch_web`` is lazily imported so that sub-modules (controller,
console, form, models, utils) can be imported without gradio installed
— this allows unit tests to run without the ``[web]`` extra.
"""


def __getattr__(name: str):
    if name == "launch_web":
        from .app import launch_web
        return launch_web
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["launch_web"]
