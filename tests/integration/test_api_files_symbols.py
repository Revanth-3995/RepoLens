"""T19: files, source slices, symbols, and relationship listing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import analyze, register_repo


def _analyzed(client: TestClient, name: str) -> str:
    repo_id = register_repo(client, name)
    analyze(client, repo_id)
    return repo_id


def test_list_files_pagination(client: TestClient):
    repo_id = _analyzed(client, "b")
    response = client.get(f"/api/repositories/{repo_id}/files", params={"limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["page"]["total"] == 3
    assert len(body["items"]) == 2
    assert body["page"]["limit"] == 2


def test_file_detail_and_source_slice(client: TestClient):
    repo_id = _analyzed(client, "a")
    full = client.get(
        f"/api/repositories/{repo_id}/files/simple.py", params={"include_source": "true"}
    )
    assert full.status_code == 200
    body = full.json()
    assert body["source_available"] is True
    assert body["content"] is not None
    assert body["content_changed_since_analysis"] is False

    sliced = client.get(
        f"/api/repositories/{repo_id}/files/simple.py",
        params={"include_source": "true", "start_line": 1, "end_line": 2},
    )
    assert sliced.status_code == 200
    assert sliced.json()["start_line"] == 1
    assert sliced.json()["end_line"] == 2


def test_file_detail_without_source(client: TestClient):
    repo_id = _analyzed(client, "a")
    response = client.get(f"/api/repositories/{repo_id}/files/simple.py")
    assert response.status_code == 200
    assert response.json()["content"] is None


def test_unknown_file_returns_404(client: TestClient):
    repo_id = _analyzed(client, "a")
    response = client.get(f"/api/repositories/{repo_id}/files/nope.py")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"


def test_symbol_filters_and_detail(client: TestClient):
    repo_id = _analyzed(client, "c")

    classes = client.get(f"/api/repositories/{repo_id}/symbols", params={"kind": "class"}).json()
    names = {s["name"] for s in classes["items"]}
    assert {"Animal", "Dog", "GuideDog"}.issubset(names)

    exact = client.get(f"/api/repositories/{repo_id}/symbols", params={"name": "speak"}).json()
    assert exact["page"]["total"] >= 1

    symbol_id = exact["items"][0]["id"]
    detail = client.get(f"/api/symbols/{symbol_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == symbol_id


def test_symbol_parent_and_children(client: TestClient):
    repo_id = _analyzed(client, "c")
    symbols = client.get(f"/api/repositories/{repo_id}/symbols", params={"limit": 500}).json()[
        "items"
    ]
    by_name = {s["name"]: s for s in symbols}

    animal = by_name["Animal"]
    children = client.get(f"/api/symbols/{animal['id']}/children").json()
    assert [c["name"] for c in children] == ["speak"]

    speak_of_animal = next(
        s for s in symbols if s["name"] == "speak" and s["qualified_name"] == "core.Animal.speak"
    )
    parent = client.get(f"/api/symbols/{speak_of_animal['id']}/parent")
    assert parent.status_code == 200
    assert parent.json()["name"] == "Animal"


def test_unknown_symbol_returns_404(client: TestClient):
    response = client.get("/api/symbols/sym_missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "symbol_not_found"


def test_relationship_status_filter(client: TestClient):
    repo_id = _analyzed(client, "d")
    all_rels = client.get(
        f"/api/repositories/{repo_id}/relationships", params={"limit": 500}
    ).json()
    assert all_rels["page"]["total"] >= 1

    external = client.get(
        f"/api/repositories/{repo_id}/relationships",
        params={"status": "external", "limit": 500},
    ).json()
    assert all(r["resolution_status"] == "external" for r in external["items"])
    assert external["page"]["total"] >= 1


def test_invalid_filter_value_returns_422(client: TestClient):
    repo_id = _analyzed(client, "a")
    response = client.get(f"/api/repositories/{repo_id}/relationships", params={"status": "bogus"})
    assert response.status_code == 422


def test_relationship_records_carry_source_rel_path(client: TestClient):
    """Every relationship must name the file it was extracted from."""
    repo_id = _analyzed(client, "b")
    items = client.get(f"/api/repositories/{repo_id}/relationships", params={"limit": 500}).json()[
        "items"
    ]
    assert items, "fixture b must produce relationships"
    assert all(r["source_rel_path"] for r in items)
    assert all(r["source_rel_path"].endswith(".py") for r in items)
