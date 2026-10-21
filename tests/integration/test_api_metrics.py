"""Phase 2 slice 1: function metrics API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import analyze, symbol_id_for


def test_function_metrics_endpoint(client: TestClient, tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "m.py").write_text(
        "def f(x, y):\n    return x + y\n\ndef g(a):\n    if a:\n        return 1\n    return 0\n",
        encoding="utf-8",
    )
    resp = client.post("/api/repositories", json={"name": "m", "path": str(repo)})
    assert resp.status_code == 201
    repo_id = resp.json()["id"]
    analyze(client, repo_id)

    r = client.get(f"/api/repositories/{repo_id}/metrics/functions")
    assert r.status_code == 200
    body = r.json()
    assert body["page"]["total"] == 2
    names = {it["qualified_name"] for it in body["items"]}
    assert names == {"m.f", "m.g"}
    g = next(it for it in body["items"] if it["qualified_name"] == "m.g")
    assert g["statement_count"] == 3
    assert g["if_count"] == 1
    assert g["cyclomatic_complexity"] == 2


def test_decorated_function_metrics_keep_the_symbol_association(client: TestClient, tmp_path):
    repo = tmp_path / "decorated"
    repo.mkdir()
    (repo / "m.py").write_text(
        "def deco(fn):\n    return fn\n\n@deco\ndef decorated(x):\n    return x\n",
        encoding="utf-8",
    )
    response = client.post("/api/repositories", json={"name": "decorated", "path": str(repo)})
    assert response.status_code == 201
    repo_id = response.json()["id"]
    analyze(client, repo_id)

    decorated_id = symbol_id_for(client, repo_id, "m.decorated")
    metrics = client.get(
        f"/api/repositories/{repo_id}/metrics/functions", params={"limit": 500}
    ).json()
    item = next(m for m in metrics["items"] if m["qualified_name"] == "m.decorated")
    assert item["symbol_id"] == decorated_id
    assert item["start_line"] == 5
    assert item["end_line"] == 6
