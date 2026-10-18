"""T14: graph query API (nodes, edges, neighbors, path, stats)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import analyze, register_repo


def _analyzed(client: TestClient, name: str) -> str:
    repo_id = register_repo(client, name)
    analyze(client, repo_id)
    return repo_id


def test_graph_stats(client: TestClient):
    repo_id = _analyzed(client, "c")
    response = client.get(f"/api/repositories/{repo_id}/graph/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["nodes"] > 0 and body["edges"] > 0
    assert body["by_node_type"].get("file") == 2


def test_graph_nodes_type_filter_and_single_node(client: TestClient):
    repo_id = _analyzed(client, "c")
    nodes = client.get(f"/api/repositories/{repo_id}/graph/nodes", params={"type": "file"}).json()
    assert nodes["page"]["total"] == 2
    assert all(n["type"] == "file" for n in nodes["items"])

    node_id = nodes["items"][0]["id"]
    single = client.get(f"/api/graph/nodes/{node_id}")
    assert single.status_code == 200
    assert single.json()["id"] == node_id


def test_graph_edges_type_filter(client: TestClient):
    repo_id = _analyzed(client, "c")
    edges = client.get(
        f"/api/repositories/{repo_id}/graph/edges", params={"type": "contains"}
    ).json()
    assert edges["page"]["total"] > 0
    assert all(e["type"] == "contains" for e in edges["items"])


def test_graph_neighbors_and_404(client: TestClient):
    repo_id = _analyzed(client, "c")
    files = client.get(f"/api/repositories/{repo_id}/graph/nodes", params={"type": "file"}).json()[
        "items"
    ]
    node_id = files[0]["id"]

    response = client.get(f"/api/graph/nodes/{node_id}/neighbors", params={"direction": "out"})
    assert response.status_code == 200
    body = response.json()
    assert body["node"]["id"] == node_id
    assert body["neighbors"]
    assert all(e["source"] == node_id for e in body["edges"])

    missing = client.get("/api/graph/nodes/nope/neighbors")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "graph_node_not_found"


def test_graph_path_found_and_missing(client: TestClient):
    repo_id = _analyzed(client, "c")
    symbols = client.get(
        f"/api/repositories/{repo_id}/symbols",
        params={"name": "speak", "kind": "method", "limit": 500},
    ).json()["items"]
    target = next(s for s in symbols if s["qualified_name"] == "core.Animal.speak")

    response = client.get(
        "/api/graph/path",
        params={"source": target["file_id"], "target": target["id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["nodes"][0] == target["file_id"]
    assert body["nodes"][-1] == target["id"]

    none = client.get("/api/graph/path", params={"source": "nope-a", "target": "nope-b"})
    assert none.status_code == 200
    assert none.json()["found"] is False
    assert none.json()["reason"]
