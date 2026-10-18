"""Deterministic, stable identifier derivation (decision AD-1).

All identifiers are opaque strings of the form ``prefix_<24 hex chars>``
derived with SHA-256 from documented, stable inputs. Line numbers are
deliberately NOT part of symbol identity; an ordinal counter disambiguates
same-named redefinitions within one file.

Stability scope (honest guarantee, see docs/architecture.md section 4):
- Stable under edits elsewhere in the file or repository.
- Changes when a symbol is renamed, moves scope/file, changes kind, or when
  an earlier duplicate redefinition is added/removed (ordinal shift).
"""

from __future__ import annotations

import hashlib

_SEPARATOR = "\x1f"


def stable_id(prefix: str, *parts: str) -> str:
    """Derive a deterministic id from the given parts."""
    digest = hashlib.sha256(_SEPARATOR.join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def normalize_absolute_path(path: str) -> str:
    """Normalize an absolute path for identity purposes.

    Backslashes become forward slashes and trailing slashes are stripped so
    that identity derivation is insensitive to separator style.
    """
    normalized = path.replace("\\", "/").rstrip("/")
    return normalized or "/"


def repo_id_for(root_path: str) -> str:
    """Stable repository id derived from the normalized absolute root path."""
    return stable_id("repo", normalize_absolute_path(root_path))


def file_id_for(repository_id: str, rel_path: str) -> str:
    """Stable file id for a repository-relative POSIX path."""
    return stable_id("file", repository_id, rel_path)


def symbol_id_for(
    repository_id: str, rel_path: str, kind: str, qualified_name: str, ordinal: int
) -> str:
    """Stable symbol id.

    ``ordinal`` is the 0-based occurrence index of the same
    (qualified_name, kind) pair within the file.
    """
    return stable_id("sym", repository_id, rel_path, kind, qualified_name, str(ordinal))


def relationship_id_for(
    repository_id: str,
    source_file_id: str,
    source_symbol_id: str | None,
    rel_type: str,
    target_symbol_id: str | None,
    target_ref: str,
    line: int,
    col: int,
) -> str:
    """Deterministic relationship id; doubles as the in-run dedupe key."""
    return stable_id(
        "rel",
        repository_id,
        source_file_id,
        source_symbol_id or "",
        rel_type,
        target_symbol_id or "",
        target_ref,
        str(line),
        str(col),
    )


def new_run_id() -> str:
    """Per-run identifier (not content-derived)."""
    import uuid

    return f"run_{uuid.uuid4().hex}"
