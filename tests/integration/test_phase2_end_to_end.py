"""Phase 2 end-to-end: registered repository -> every comprehension feature.

The fixture deliberately contains multi-file imports and calls, nested
functions, branches, loops, exception handling, a dependency cycle, an empty
file, a parse-error file, and a file that writes a marker if it is ever
executed. The marker assertion proves the analyzer is static.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import analyze, make_repo, symbol_id_for


def _source_files() -> dict[str, str]:
    return {
        "pkg/__init__.py": "",
        "pkg/util.py": (
            "def helper(value):\n    return value + 1\n\ndef unused():\n    return 0\n"
        ),
        "pkg/service.py": (
            "from pkg.util import helper\n"
            "\n"
            "def compute(flag):\n"
            "    total = 0\n"
            "    for i in range(3):\n"
            "        if i == flag:\n"
            "            continue\n"
            "        total = helper(total + i)\n"
            "    try:\n"
            "        total = total / 1\n"
            "    except ZeroDivisionError:\n"
            "        total = 0\n"
            "    return total\n"
        ),
        "pkg/main.py": (
            "from pkg.service import compute\n"
            "\n"
            "def run():\n"
            "    return compute(2)\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    run()\n"
        ),
        "pkg/nested.py": (
            "def outer(value):\n    def inner(x):\n        return x + 1\n    return inner(value)\n"
        ),
        "broken.py": "def broken(:\n    pass\n",
        "empty.py": "",
        "cycle_a.py": "from cycle_b import b\n\ndef a():\n    return b()\n",
        "cycle_b.py": "from cycle_a import a\n\ndef b():\n    return 1\n",
    }


def test_full_phase2_workflow_on_a_registered_repository(client: TestClient, tmp_path):
    repo = make_repo(tmp_path, _source_files())
    marker = tmp_path / "EXECUTED_MARKER"
    (repo / "danger.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('analyzed code was executed')\n",
        encoding="utf-8",
    )
    registered = client.post("/api/repositories", json={"name": "phase2-e2e", "path": str(repo)})
    assert registered.status_code == 201, registered.text
    repo_id = registered.json()["id"]

    run = analyze(client, repo_id)
    summary = run["summary"]
    assert summary["files_discovered"] == 10
    assert summary["files_parsed"] == 9
    assert summary["files_parse_errors"] == 1
    assert summary["symbols_extracted"] == 17
    assert summary["relationships_resolved"] >= 8
    assert summary["truncated"] is False
    assert not marker.exists(), "analysis must never execute repository code"

    # 1. Persisted facts and metrics.
    symbols = client.get(f"/api/repositories/{repo_id}/symbols", params={"limit": 500}).json()[
        "items"
    ]
    by_name = {item["qualified_name"]: item for item in symbols}
    assert set(by_name) >= {
        "pkg.util.helper",
        "pkg.service.compute",
        "pkg.main.run",
        "pkg.nested",
        "pkg.nested.outer",
        "pkg.nested.outer.inner",
    }
    outer = by_name["pkg.nested.outer"]
    inner = by_name["pkg.nested.outer.inner"]
    metrics = client.get(
        f"/api/repositories/{repo_id}/metrics/functions", params={"limit": 500}
    ).json()
    assert metrics["page"]["total"] == 8
    by_metric = {item["qualified_name"]: item for item in metrics["items"]}
    assert by_metric["pkg.service.compute"]["cyclomatic_complexity"] == 4
    assert by_metric["pkg.nested.outer"]["cyclomatic_complexity"] == 1
    assert by_metric["pkg.nested.outer"]["call_count"] == 1
    assert by_metric["pkg.nested.outer"]["symbol_id"] == outer["id"]
    assert by_metric["pkg.nested.outer.inner"]["symbol_id"] == inner["id"]

    # 2. Nested function structure: the inner body is not part of outer's CFG.
    cfg = client.get(f"/api/symbols/{outer['id']}/cfg").json()
    assert [node["kind"] for node in cfg["nodes"]] == [
        "entry",
        "exit",
        "nested_def",
        "return",
    ]
    assert all(node["line"] != 3 for node in cfg["nodes"])
    assert [(edge["source"], edge["target"], edge["type"]) for edge in cfg["edges"]] == [
        ("n0", "n2", "normal"),
        ("n2", "n3", "normal"),
        ("n3", "n1", "return"),
    ]
    assert cfg["notes"] == [] or all("nested" not in note for note in cfg["notes"])

    # 3. Local data flow for outer: value/inner definitions and the inner call.
    dataflow = client.get(f"/api/symbols/{outer['id']}/dataflow").json()
    assert [(item["name"], item["kind"]) for item in dataflow["definitions"]] == [
        ("value", "parameter"),
        ("inner", "nested_def"),
    ]
    assert {item["name"] for item in dataflow["uses"]} == {"inner", "value"}
    assert dataflow["undefined_uses"] == []

    # 4. Explanations at statement, function, file, folder, and project levels.
    statement = client.get(
        f"/api/repositories/{repo_id}/explanations/statement/{outer['id']}",
        params={"line": 4},
    )
    assert statement.status_code == 200, statement.text
    assert statement.json()["subject"] == "pkg.nested.outer:4"
    assert any("`return` statement" in fact for fact in statement.json()["facts"])

    function = client.get(f"/api/repositories/{repo_id}/explanations/function/{outer['id']}")
    assert function.status_code == 200, function.text
    function_body = function.json()
    assert any("Control-flow graph: 4 node(s), 3 edge(s)" in f for f in function_body["facts"])
    assert any(
        "Outgoing calls: 1 reference(s), 1 resolved statically." in f
        for f in function_body["facts"]
    )
    assert any(item["symbol_id"] == outer["id"] for item in function_body["evidence"])

    file_body = client.get(f"/api/repositories/{repo_id}/explanations/file/pkg/nested.py").json()
    assert file_body["level"] == "file"
    assert any("`pkg.nested.outer` (function)" in fact for fact in file_body["facts"])

    folder_body = client.get(
        f"/api/repositories/{repo_id}/explanations/folder", params={"prefix": "pkg"}
    ).json()
    assert folder_body["level"] == "folder"
    assert "Folder `pkg` contains 5 analyzed file(s)." in folder_body["facts"]
    assert "Outgoing cross-boundary dependencies: 0." in folder_body["facts"]

    project_body = client.get(f"/api/repositories/{repo_id}/explanations/project").json()
    assert project_body["level"] == "project"
    assert any("contains 10 analyzed file(s)" in fact for fact in project_body["facts"])
    assert any("Import cycles detected: 1." in fact for fact in project_body["facts"])
    assert any("pkg/main.py:6" in fact for fact in project_body["facts"])

    # 5. Search is lexical and deterministic, and finds nested definitions.
    search = client.get(f"/api/repositories/{repo_id}/search", params={"q": "inner"}).json()
    assert search["items"][0]["kind"] == "symbol"
    assert search["items"][0]["qualified_name"] == "pkg.nested.outer.inner"
    assert search["items"][0]["score"] == 100
    token = client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "inner value", "mode": "token"},
    ).json()
    assert any(
        item["line"] == 4 and item["qualified_name"] == "pkg.nested.outer"
        for item in token["items"]
    )

    # 6. Forward tracing follows the nested call; reverse tracing shows callers.
    forward = client.get(
        f"/api/repositories/{repo_id}/trace",
        params={"symbol_id": outer["id"], "direction": "out", "rel_types": "calls"},
    ).json()
    assert [(node["qualified_name"], node["depth"]) for node in forward["nodes"]] == [
        ("pkg.nested.outer", 0),
        ("pkg.nested.outer.inner", 1),
    ]
    assert len(forward["edges"]) == 1
    assert forward["edges"][0]["target_symbol_id"] == inner["id"]

    backward = client.get(
        f"/api/repositories/{repo_id}/trace",
        params={"symbol_id": inner["id"], "direction": "in", "rel_types": "calls"},
    ).json()
    assert [(node["qualified_name"], node["depth"]) for node in backward["nodes"]] == [
        ("pkg.nested.outer.inner", 0),
        ("pkg.nested.outer", 1),
    ]

    # 7. Architecture: cycle, entry point, hotspots, parse error, findings.
    architecture = client.get(f"/api/repositories/{repo_id}/architecture").json()
    assert architecture["files"] == 10
    assert architecture["dependency_edges"] == 4
    assert architecture["cycles"][0]["files"] == [
        "cycle_a.py",
        "cycle_b.py",
        "cycle_a.py",
    ]
    assert architecture["entry_points"] == [
        {
            "rel_path": "pkg/main.py",
            "line": 6,
            "evidence": "explicit if __name__ == '__main__' guard",
        }
    ]
    assert architecture["complexity_hotspots"][0]["qualified_name"] == "pkg.service.compute"
    kinds = {finding["kind"] for finding in architecture["findings"]}
    assert {"dependency_cycle", "disconnected_components", "central_component"} <= kinds
    cycle_finding = next(
        finding for finding in architecture["findings"] if finding["kind"] == "dependency_cycle"
    )
    assert cycle_finding["heuristic"] is False
    assert cycle_finding["evidence"]
    assert any("not that the code is wrong" in cycle_finding["description"] for _ in [0])

    # 8. Re-analysis replaces the latest run's details and keeps the workflow valid.
    old_compute_id = symbol_id_for(client, repo_id, "pkg.service.compute")
    (repo / "pkg" / "service.py").write_text(
        "from pkg.util import helper\n"
        "\n"
        "def compute_new(flag):\n"
        "    total = 0\n"
        "    for i in range(3):\n"
        "        total = helper(total + i)\n"
        "    return total\n",
        encoding="utf-8",
    )
    analyze(client, repo_id)
    assert client.get(f"/api/symbols/{old_compute_id}/cfg").status_code == 404
    new_metrics = client.get(
        f"/api/repositories/{repo_id}/metrics/functions", params={"limit": 500}
    ).json()
    new_names = {item["qualified_name"] for item in new_metrics["items"]}
    assert "pkg.service.compute" not in new_names
    assert "pkg.service.compute_new" in new_names
    assert (
        next(
            item
            for item in new_metrics["items"]
            if item["qualified_name"] == "pkg.service.compute_new"
        )["cyclomatic_complexity"]
        == 2
    )
    new_architecture = client.get(f"/api/repositories/{repo_id}/architecture").json()
    assert new_architecture["complexity_hotspots"][0]["qualified_name"] == "pkg.service.compute_new"
    assert not marker.exists(), "re-analysis must never execute repository code"
