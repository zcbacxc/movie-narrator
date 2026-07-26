"""Root-level pytest configuration.

Tells pytest to only collect from the tests/ directory and ignore
non-test directories that may exist as local leftovers.
"""

collect_ignore = ["webui", "node_modules", ".mimocode"]
