"""T6 / T20: path-traversal and root-boundary defenses."""

from __future__ import annotations

import pytest

from repolens.core.errors import PathTraversalError
from repolens.services.analysis_service import validate_rel_path


@pytest.mark.parametrize(
    "value",
    [
        "/etc/passwd",
        "~/secret",
        "C:/Windows/system32",
        "a/../../b.py",
        "../b.py",
        "..",
        "",
        "   ",
    ],
)
def test_validate_rel_path_rejects_unsafe(value: str):
    with pytest.raises(PathTraversalError):
        validate_rel_path(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a/b.py", "a/b.py"),
        ("a\\b.py", "a/b.py"),
        ("a/./b.py", "a/b.py"),
        ("a//b.py", "a/b.py"),
        ("  a/b.py  ", "a/b.py"),
    ],
)
def test_validate_rel_path_normalizes(value: str, expected: str):
    assert validate_rel_path(value) == expected


def test_file_detail_rejects_traversal(client):
    from tests.conftest import analyze, register_repo

    repo_id = register_repo(client, "a")
    analyze(client, repo_id)
    # The router collapses ../; the service still validates what reaches it.
    response = client.get(f"/api/repositories/{repo_id}/files/{'../secret.py'}")
    assert response.status_code in (400, 404)
