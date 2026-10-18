"""T22: end-to-end behavior over fixture repositories A-D."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import analyze, register_repo


def _analyzed(client: TestClient, name: str) -> dict:
    repo_id = register_repo(client, name)
    return {"repo_id": repo_id, "run": analyze(client, repo_id)}


def test_fixture_a_end_to_end(client: TestClient):
    ctx = _analyzed(client, "a")
    repo_id = ctx["repo_id"]
    files = client.get(f"/api/repositories/{repo_id}/files").json()
    assert {f["rel_path"] for f in files["items"]} == {"simple.py"}

    edges = client.get(f"/api/repositories/{repo_id}/graph/edges", params={"type": "calls"}).json()
    assert edges["page"]["total"] >= 1  # simple.add is resolvable


def test_fixture_b_package_relative_imports(client: TestClient):
    ctx = _analyzed(client, "b")
    repo_id = ctx["repo_id"]
    rels = client.get(
        f"/api/repositories/{repo_id}/relationships",
        params={"rel_type": "imports", "status": "resolved_static", "limit": 500},
    ).json()
    refs = {r["target_ref"] for r in rels["items"]}
    assert any("package" in ref for ref in refs)

    files = client.get(f"/api/repositories/{repo_id}/files").json()
    assert {f["rel_path"] for f in files["items"]} == {
        "main.py",
        "package/__init__.py",
        "package/mod.py",
    }


def test_fixture_c_inheritance_edges(client: TestClient):
    ctx = _analyzed(client, "c")
    repo_id = ctx["repo_id"]
    edges = client.get(
        f"/api/repositories/{repo_id}/graph/edges", params={"type": "inherits_from"}
    ).json()
    assert edges["page"]["total"] >= 1


def test_fixture_d_is_honest_about_failures(client: TestClient):
    ctx = _analyzed(client, "d")
    repo_id = ctx["repo_id"]
    # No resolved relationships => only structural `contains` edges exist.
    edges = client.get(f"/api/repositories/{repo_id}/graph/edges", params={"limit": 500}).json()
    assert edges["page"]["total"] > 0
    assert {e["type"] for e in edges["items"]} == {"contains"}

    errors = client.get(f"/api/analysis/runs/{ctx['run']['id']}/errors").json()
    assert errors["page"]["total"] >= 2


@pytest.mark.parametrize("name", ["a", "b", "c", "d"])
def test_every_fixture_produces_run_and_graph(client: TestClient, name: str):
    ctx = _analyzed(client, name)
    repo_id = ctx["repo_id"]
    stats = client.get(f"/api/repositories/{repo_id}/graph/stats").json()
    files = client.get(f"/api/repositories/{repo_id}/files").json()
    # Every parsed/read/error file has a node; graph is never empty.
    assert stats["nodes"] >= files["page"]["total"]
    assert ctx["run"]["status"] == "completed"
