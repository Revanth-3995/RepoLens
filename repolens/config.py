"""Environment-based configuration for RepoLens.

Settings are a frozen dataclass populated from ``REPOLENS_*`` environment
variables (decision AD-11 in docs/architecture.md). Tests construct
``Settings`` values directly, so no environment mutation is needed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

ENV_PREFIX = "REPOLENS_"

DEFAULT_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".eggs",
        "site-packages",
        "htmlcov",
        ".idea",
        ".vscode",
        "pip-wheel-metadata",
    }
)


def _env_str(name: str, default: str) -> str:
    return os.getenv(ENV_PREFIX + name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(ENV_PREFIX + name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"Environment variable {ENV_PREFIX}{name} must be an integer, got {raw!r}"
        ) from None
    if value <= 0:
        raise ValueError(f"Environment variable {ENV_PREFIX}{name} must be positive, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    db_path: Path = Path("repolens.db")
    max_file_bytes: int = 1_000_000
    max_files: int = 5_000
    path_max_depth: int = 6
    path_max_nodes: int = 20_000
    page_limit_default: int = 50
    page_limit_max: int = 500
    # Phase 2 resource bounds (all deterministic, all reported when hit).
    cfg_max_nodes: int = 5_000
    cfg_max_edges: int = 20_000
    cfg_max_depth: int = 200
    dataflow_max_definitions: int = 5_000
    dataflow_max_uses: int = 10_000
    dataflow_max_iterations: int = 50_000
    trace_max_depth: int = 4
    trace_max_nodes: int = 500
    search_max_files: int = 2_000
    search_max_hits: int = 1_000
    architecture_max_cycles: int = 50
    architecture_max_cycle_length: int = 10
    architecture_top_components: int = 10
    architecture_max_findings: int = 100
    architecture_entry_scan_limit: int = 500
    complexity_warning_threshold: int = 10
    fan_out_warning_threshold: int = 10
    fan_in_warning_threshold: int = 10
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000
    excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from ``REPOLENS_*`` environment variables."""
        return cls(
            db_path=Path(_env_str("DB_PATH", "repolens.db")),
            max_file_bytes=_env_int("MAX_FILE_BYTES", 1_000_000),
            max_files=_env_int("MAX_FILES", 5_000),
            path_max_depth=min(_env_int("PATH_MAX_DEPTH", 6), 10),
            path_max_nodes=_env_int("PATH_MAX_NODES", 20_000),
            trace_max_depth=min(_env_int("TRACE_MAX_DEPTH", 4), 8),
            trace_max_nodes=min(_env_int("TRACE_MAX_NODES", 500), 5_000),
            search_max_files=_env_int("SEARCH_MAX_FILES", 2_000),
            search_max_hits=_env_int("SEARCH_MAX_HITS", 1_000),
            complexity_warning_threshold=_env_int("COMPLEXITY_WARNING_THRESHOLD", 10),
            fan_out_warning_threshold=_env_int("FAN_OUT_WARNING_THRESHOLD", 10),
            fan_in_warning_threshold=_env_int("FAN_IN_WARNING_THRESHOLD", 10),
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
            host=_env_str("HOST", "127.0.0.1"),
            port=_env_int("PORT", 8000),
        )


def configure_logging(settings: Settings) -> None:
    """Configure root logging once, at a level from settings."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
