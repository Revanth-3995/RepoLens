"""T17 / T18: analysis runs, summaries, and error reporting over HTTP."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import analyze, register_repo


def test_trigger_analysis_returns_completed_run(client: TestClient):
    repo_id = register_repo(client, "a")
    run = analyze(client, repo_id)
    assert run["status"] == "completed"
    assert run["summary"]["files_discovered"] == 1
    assert run["summary"]["files_parsed"] == 1
    assert run["summary"]["symbols_extracted"] > 0
    assert run["summary"]["duration_seconds"] >= 0


def test_summary_counts_reflect_real_errors(client: TestClient):
    repo_id = register_repo(client, "d")
    run = analyze(client, repo_id)
    summary = run["summary"]
    assert summary["files_discovered"] == 3
    assert summary["files_parsed"] == 1
    assert summary["files_parse_errors"] == 1
    assert summary["files_read_errors"] == 1
    assert summary["files_read"] == 2
    assert summary["errors"] >= 2  # parse_error + read_error
    assert summary["relationships_external"] >= 1


def test_run_history_and_lookup(client: TestClient):
    repo_id = register_repo(client, "a")
    first = analyze(client, repo_id)
    second = analyze(client, repo_id)

    history = client.get(f"/api/repositories/{repo_id}/runs")
    assert history.status_code == 200
    ids = [r["id"] for r in history.json()]
    assert second["id"] in ids and first["id"] in ids

    fetched = client.get(f"/api/analysis/runs/{second['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == second["id"]


def test_analysis_errors_are_retrievable(client: TestClient):
    repo_id = register_repo(client, "d")
    run = analyze(client, repo_id)
    response = client.get(f"/api/analysis/runs/{run['id']}/errors")
    assert response.status_code == 200
    body = response.json()
    categories = {item["category"] for item in body["items"]}
    assert "parse_error" in categories
    assert "read_error" in categories
    assert body["page"]["total"] >= 2


def test_unknown_run_returns_404(client: TestClient):
    response = client.get("/api/analysis/runs/run_missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "analysis_run_not_found"


def test_trigger_unknown_repository_returns_404(client: TestClient):
    response = client.post("/api/repositories/repo_missing/analysis")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "repository_not_found"
