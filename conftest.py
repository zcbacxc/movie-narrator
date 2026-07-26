"""Root-level pytest configuration.

Tells pytest to only collect from the tests/ directory and ignore
non-test directories that may exist as local leftovers.

``webui`` is listed because a phantom empty directory may linger on
some developer machines after the monorepo split; it is not tracked
by git and contains no Python files, but pytest's directory scanner
can raise ``FileNotFoundError`` when it tries to stat the entry.
"""

collect_ignore = [".mimocode", "webui"]
