"""T9: deterministic identifiers and collision avoidance."""

from __future__ import annotations

from repolens.core.identifiers import (
    file_id_for,
    new_run_id,
    normalize_absolute_path,
    relationship_id_for,
    repo_id_for,
    stable_id,
    symbol_id_for,
)


def test_stable_id_is_deterministic_and_prefixed():
    a = stable_id("x", "one", "two")
    b = stable_id("x", "one", "two")
    assert a == b
    assert a.startswith("x_")
    assert len(a) == len("x_") + 24


def test_stable_id_separator_prevents_concatenation_collisions():
    # ("ab", "c") must not equal ("a", "bc")
    assert stable_id("p", "ab", "c") != stable_id("p", "a", "bc")


def test_repo_id_ignores_separator_style_and_trailing_slash():
    forward = repo_id_for("C:/work/project/")
    back = repo_id_for("C:\\work\\project")
    assert forward == back


def test_normalize_absolute_path():
    assert normalize_absolute_path("C:\\a\\b\\") == "C:/a/b"
    assert normalize_absolute_path("/") == "/"


def test_file_id_depends_on_repo_and_path():
    assert file_id_for("r1", "a.py") != file_id_for("r2", "a.py")
    assert file_id_for("r1", "a.py") != file_id_for("r1", "b.py")


def test_symbol_id_disambiguates_by_ordinal_and_kind():
    base = symbol_id_for("r1", "a.py", "function", "m.f", 0)
    assert base == symbol_id_for("r1", "a.py", "function", "m.f", 0)
    assert base != symbol_id_for("r1", "a.py", "function", "m.f", 1)
    assert base != symbol_id_for("r1", "a.py", "method", "m.f", 0)


def test_relationship_id_is_stable_and_dedupe_key():
    args = ("r1", "f1", None, "imports", None, "os", 3, 0)
    assert relationship_id_for(*args) == relationship_id_for(*args)
    changed = ("r1", "f1", None, "imports", None, "os", 4, 0)
    assert relationship_id_for(*args) != relationship_id_for(*changed)


def test_new_run_id_is_unique_and_prefixed():
    a, b = new_run_id(), new_run_id()
    assert a.startswith("run_") and b.startswith("run_")
    assert a != b
