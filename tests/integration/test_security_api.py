"""T6 / T20: source retrieval safety and root-boundary enforcement over HTTP."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import analyze, fixture_repo, register_repo


def _copy_fixture(tmp_path: Path, name: str) -> Path:
    root = tmp_path / "copy"
    shutil.copytree(fixture_repo(name), root)
    return root


def test_source_is_reread_not_stored_and_tracks_changes(client: TestClient, tmp_path: Path):
    root = _copy_fixture(tmp_path, "a")
    repo_id = register_repo(client, "copy-a", root)
    analyze(client, repo_id)

    before = client.get(
        f"/api/repositories/{repo_id}/files/simple.py", params={"include_source": "true"}
    ).json()
    assert before["source_available"] is True
    assert before["content_changed_since_analysis"] is False

    (root / "simple.py").write_text("# changed\ndef z():\n    return 1\n", encoding="utf-8")
    after = client.get(
        f"/api/repositories/{repo_id}/files/simple.py", params={"include_source": "true"}
    ).json()
    assert after["content_changed_since_analysis"] is True


def test_deleted_source_returns_metadata_without_content(client: TestClient, tmp_path: Path):
    root = _copy_fixture(tmp_path, "a")
    repo_id = register_repo(client, "copy-a", root)
    analyze(client, repo_id)

    (root / "simple.py").unlink()
    response = client.get(
        f"/api/repositories/{repo_id}/files/simple.py", params={"include_source": "true"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_available"] is False
    assert body["content"] is None
    assert body["module_name"] == "simple"  # persisted metadata survives


def test_traversal_attempts_are_blocked(client: TestClient, tmp_path: Path):
    root = _copy_fixture(tmp_path, "a")
    # Create a file just outside the registered root that must never be served.
    secret = tmp_path / "secret.py"
    secret.write_text("SECRET = 'do not leak'\n", encoding="utf-8")

    repo_id = register_repo(client, "copy-a", root)
    analyze(client, repo_id)

    for path in ("../secret.py", "..%2Fsecret.py", "%2e%2e%2fsecret.py", "sub/../../secret.py"):
        response = client.get(
            f"/api/repositories/{repo_id}/files/{path}", params={"include_source": "true"}
        )
        assert response.status_code in (400, 404), path
        assert "SECRET" not in response.text
        assert "do not leak" not in response.text


def test_error_responses_do_not_leak_absolute_paths(client: TestClient, tmp_path: Path):
    root = _copy_fixture(tmp_path, "a")
    repo_id = register_repo(client, "copy-a", root)
    analyze(client, repo_id)
    response = client.get(f"/api/repositories/{repo_id}/files/missing.py")
    assert response.status_code == 404
    assert str(root) not in response.text
    assert str(tmp_path) not in response.text
