"""T3: repository registration API end to end."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import fixture_repo


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_documents_expected_paths(client: TestClient):
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    assert "/api/repositories" in paths
    assert "/api/repositories/{repository_id}/analysis" in paths
    assert "/api/graph/path" in paths


def test_register_list_and_get(client: TestClient):
    created = client.post(
        "/api/repositories",
        json={"name": "fixture-a", "path": str(fixture_repo("a"))},
    )
    assert created.status_code == 201
    repo = created.json()
    assert repo["id"].startswith("repo_")
    assert repo["latest_run"] is None

    listed = client.get("/api/repositories")
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()] == [repo["id"]]

    fetched = client.get(f"/api/repositories/{repo['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == repo["id"]


def test_register_invalid_path_returns_400_envelope(client: TestClient, tmp_path: Path):
    response = client.post(
        "/api/repositories",
        json={"name": "nope", "path": str(tmp_path / "missing")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "invalid_repository_path"
    assert "detail" in body["error"]


def test_duplicate_registration_returns_409(client: TestClient):
    payload = {"name": "dup", "path": str(fixture_repo("a"))}
    assert client.post("/api/repositories", json=payload).status_code == 201
    second = client.post("/api/repositories", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "repository_exists"


def test_unknown_repository_returns_404(client: TestClient):
    response = client.get("/api/repositories/repo_missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "repository_not_found"


def test_validation_error_for_missing_name(client: TestClient):
    response = client.post("/api/repositories", json={"path": str(fixture_repo("a"))})
    assert response.status_code == 422
