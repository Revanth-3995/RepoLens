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
