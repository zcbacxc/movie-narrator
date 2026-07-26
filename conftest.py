"""Root-level pytest configuration.

Tells pytest to only collect from the tests/ directory and ignore
non-test directories (packages/, webui/, etc.).
"""

collect_ignore = ["packages", "webui", "node_modules", ".mimocode"]
