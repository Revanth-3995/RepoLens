"""Safe, deterministic discovery of Python source files under a root.

Security properties (docs/security.md):
- Never traverses directory symlinks or directory junctions (reparse points).
- Never includes file links whose resolved target escapes the root.
- Rejects oversized files with an explicit reason.
- Stops at the configured file-count limit and reports truncation.
- Deterministic order: directories and files are sorted at every level and the
  final file list is sorted by relative path.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from repolens.config import Settings
from repolens.ingestion.filters import exclusion_reason, is_excluded_dir

PYTHON_SUFFIX = ".py"


@dataclass(frozen=True)
class DiscoveredFile:
    """A Python file accepted for analysis."""

    path: Path
    rel_path: str  # POSIX-style, relative to the resolved root
    size_bytes: int


@dataclass(frozen=True)
class ExcludedEntry:
    """A path rejected during discovery, with the reason it was rejected."""

    rel_path: str
    reason: str


@dataclass
class DiscoveryResult:
    files: list[DiscoveredFile] = field(default_factory=list)
    excluded_dirs: list[ExcludedEntry] = field(default_factory=list)
    excluded_files: list[ExcludedEntry] = field(default_factory=list)
    discovered_count: int = 0  # candidate .py files examined before any limit stopped discovery
    truncated: bool = False


def is_within_root(root: Path, candidate: Path) -> bool:
    """True if ``candidate`` (after resolution) stays inside ``root``.

    Both paths are fully resolved before comparison so symlinked parents and
    ``..`` segments cannot escape.
    """
    try:
        candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def is_link_like(path: Path) -> bool:
    """True for symlinks, directory junctions and other reparse points.

    ``Path.is_symlink()`` returns ``False`` for an NTFS directory junction,
    which is a reparse point that can be created without any privileges
    (``mklink /J``). Junctions must therefore be treated like directory
    symlinks: never traversed. For file reparse points the same policy as
    file symlinks applies — the resolved target must stay inside the
    registered root. On non-Windows platforms this reduces to a symlink
    check.
    """
    try:
        if path.is_symlink():
            return True
    except OSError:
        return False

    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        try:
            return bool(is_junction())
        except OSError:
            return False

    # Fallback for Python < 3.12 (``Path.is_junction`` added in 3.12).
    try:
        st = path.lstat()
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _relative_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def discover_python_files(root: Path, settings: Settings) -> DiscoveryResult:
    """Discover analyzable ``.py`` files under ``root``.

    ``root`` must already be validated and resolved by the registration
    service; this function re-resolves defensively.
    """
    root = root.resolve()
    result = DiscoveryResult()
    excluded = settings.excluded_dirs
    max_files = settings.max_files
    max_bytes = settings.max_file_bytes

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames.sort()
        filenames.sort()
        current = Path(dirpath)

        kept_dirs: list[str] = []
        for name in dirnames:
            child = current / name
            rel = _relative_posix(root, child)
            if is_excluded_dir(name, excluded):
                result.excluded_dirs.append(ExcludedEntry(rel, exclusion_reason(name)))
                continue
            if is_link_like(child):
                # Decision AD-13: directory links (symlink or junction) are
                # never traversed.
                result.excluded_dirs.append(ExcludedEntry(rel, "symlinked_directory"))
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs

        if result.truncated:
            continue

        for name in filenames:
            if not name.endswith(PYTHON_SUFFIX):
                continue
            path = current / name
            rel = _relative_posix(root, path)
            result.discovered_count += 1

            if is_link_like(path):
                target = Path(os.path.realpath(path))
                if not is_within_root(root, target):
                    result.excluded_files.append(ExcludedEntry(rel, "symlink_escapes_root"))
                    continue

            try:
                size = path.stat().st_size
            except OSError as exc:
                result.excluded_files.append(
                    ExcludedEntry(rel, f"stat_failed ({exc.strerror or exc})")
                )
                continue

            if size > max_bytes:
                result.excluded_files.append(
                    ExcludedEntry(rel, f"file_too_large ({size} > {max_bytes} bytes)")
                )
                continue

            if len(result.files) >= max_files:
                result.truncated = True
                result.excluded_files.append(
                    ExcludedEntry(rel, "limit_exceeded (max_files reached)")
                )
                break

            result.files.append(DiscoveredFile(path=path, rel_path=rel, size_bytes=size))

        if result.truncated:
            # Prune remaining directories so the limit is enforced globally.
            dirnames[:] = []

    result.files.sort(key=lambda f: f.rel_path)
    result.excluded_dirs.sort(key=lambda e: e.rel_path)
    result.excluded_files.sort(key=lambda e: e.rel_path)
    return result
