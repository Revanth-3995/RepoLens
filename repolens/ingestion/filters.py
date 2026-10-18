"""Exclusion policy for discovery (documented in docs/security.md)."""

from __future__ import annotations

from repolens.config import DEFAULT_EXCLUDED_DIRS


def is_excluded_dir(name: str, excluded: frozenset[str] = DEFAULT_EXCLUDED_DIRS) -> bool:
    """Return True if a directory name must be pruned during discovery.

    Matches the default exclusion set (version control, virtualenvs, caches,
    build/dist output, node_modules, IDE metadata) plus ``*.egg-info``
    packages. Exclusions are configurable through ``Settings.excluded_dirs``.
    """
    if name in excluded:
        return True
    return name.endswith(".egg-info")


def exclusion_reason(name: str) -> str:
    """Human-readable reason for pruning a directory."""
    if name.endswith(".egg-info"):
        return "excluded_directory (*.egg-info)"
    return f"excluded_directory ({name})"
