"""T4: discovery determinism, exclusions, limits, symlink policy."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from repolens.config import Settings
from repolens.ingestion.discovery import discover_python_files, is_within_root
from tests.conftest import fixture_repo


def _write(path: Path, text: str = "x = 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_discovers_fixture_a(tmp_path: Path):
    result = discover_python_files(fixture_repo("a"), Settings())
    rels = [f.rel_path for f in result.files]
    assert rels == ["simple.py"]
    assert all(f.size_bytes > 0 for f in result.files)


def test_discovery_is_sorted_and_deterministic(tmp_path: Path):
    root = tmp_path / "repo"
    for name in ["z.py", "a.py", "m/n.py", "m/a.py"]:
        _write(root / name)
    result = discover_python_files(root, Settings())
    rels = [f.rel_path for f in result.files]
    assert rels == sorted(rels)


def test_excluded_directories_are_pruned(tmp_path: Path):
    root = tmp_path / "repo"
    _write(root / "keep.py")
    _write(root / "__pycache__" / "c.py")
    _write(root / "node_modules" / "n.py")
    _write(root / "pkg.egg-info" / "i.py")
    result = discover_python_files(root, Settings())
    assert [f.rel_path for f in result.files] == ["keep.py"]
    reasons = {e.rel_path for e in result.excluded_dirs}
    assert "__pycache__" in reasons
    assert "node_modules" in reasons
    assert "pkg.egg-info" in reasons


def test_non_python_files_ignored(tmp_path: Path):
    root = tmp_path / "repo"
    _write(root / "a.py")
    _write(root / "README.md", "hello")
    result = discover_python_files(root, Settings())
    assert [f.rel_path for f in result.files] == ["a.py"]
    assert result.discovered_count == 1


def test_oversized_file_excluded(tmp_path: Path):
    root = tmp_path / "repo"
    _write(root / "big.py", "x" * 500)
    _write(root / "small.py", "x = 1\n")
    result = discover_python_files(root, Settings(max_file_bytes=100))
    assert [f.rel_path for f in result.files] == ["small.py"]
    assert any("file_too_large" in e.reason for e in result.excluded_files)


def test_max_files_truncates_and_flags(tmp_path: Path):
    root = tmp_path / "repo"
    for i in range(5):
        _write(root / f"f{i}.py")
    result = discover_python_files(root, Settings(max_files=2))
    assert len(result.files) == 2
    assert result.truncated is True
    # discovered_count counts candidates examined before the limit stopped
    # discovery; it is therefore >= max_files and <= the number of files.
    assert 2 <= result.discovered_count <= 5


def test_directory_symlink_not_traversed(tmp_path: Path):
    root = tmp_path / "repo"
    _write(root / "real.py")
    outside = tmp_path / "outside"
    _write(outside / "secret.py")
    link = root / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - env dependent
        pytest.skip(f"cannot create directory symlink on this host: {exc}")
    result = discover_python_files(root, Settings())
    rels = [f.rel_path for f in result.files]
    assert rels == ["real.py"]
    assert any(e.reason == "symlinked_directory" for e in result.excluded_dirs)


def test_symlink_file_escaping_root_excluded(tmp_path: Path):
    root = tmp_path / "repo"
    _write(root / "real.py")
    secret = tmp_path / "secret.py"
    _write(secret)
    link = root / "link.py"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - env dependent
        pytest.skip(f"cannot create file symlink on this host: {exc}")
    result = discover_python_files(root, Settings())
    assert [f.rel_path for f in result.files] == ["real.py"]
    assert any(e.reason == "symlink_escapes_root" for e in result.excluded_files)


def test_directory_junction_not_traversed(tmp_path: Path):
    """Windows directory junctions must be treated like directory symlinks.

    A junction is an NTFS reparse point that ``Path.is_symlink()`` reports as
    ``False`` for, while ``mklink /J`` creates one **without** any privilege —
    unlike ``os.symlink``. This is the reachable escape the privilege-gated
    symlink tests above cannot cover on this host, so it is exercised
    separately. Relative paths plus ``cwd`` avoid path-quoting issues when the
    temporary directory contains spaces.
    """
    if sys.platform != "win32":
        pytest.skip("directory junctions are a Windows-only reparse point")

    root = tmp_path / "repo"
    _write(root / "real.py")
    _write(tmp_path / "outside" / "secret.py")

    subprocess.run(
        "mklink /J linked ..\\outside",
        shell=True,
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    link = root / "linked"
    if not link.is_dir():
        pytest.skip("cannot create directory junction on this host")

    # Independently confirm it really is a reparse point (not our own helper,
    # which is the code under test).
    try:
        attributes = link.lstat().st_file_attributes
    except (OSError, AttributeError):  # pragma: no cover - non-Windows
        attributes = 0
    if not attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:  # pragma: no cover
        pytest.skip("host did not create a real reparse point")

    result = discover_python_files(root, Settings())
    rels = [f.rel_path for f in result.files]
    assert rels == ["real.py"], f"junction escape: {rels}"
    assert any(e.reason == "symlinked_directory" for e in result.excluded_dirs)


def test_is_within_root(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    inside = root / "a" / "b.py"
    outside = tmp_path / "other.py"
    assert is_within_root(root, inside)
    assert not is_within_root(root, outside)
    assert not is_within_root(root, root / ".." / "escape.py")
