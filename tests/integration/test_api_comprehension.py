"""Phase 2 integration: persisted facts -> CFG/data-flow/explanations/search/trace/architecture."""

from __future__ import annotations

from collections import Counter

from fastapi.testclient import TestClient

from tests.conftest import analyze, analyzed_temp_repo, make_repo, symbol_id_for


def _files() -> dict[str, str]:
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
        "broken.py": "def broken(:\n    pass\n",
        "empty.py": "",
        "cycle_a.py": "from cycle_b import b\n\ndef a():\n    return b()\n",
        "cycle_b.py": "from cycle_a import a\n\ndef b():\n    return 1\n",
    }


def _setup(client: TestClient, tmp_path) -> tuple[str, dict[str, dict]]:
    repo_id = analyzed_temp_repo(client, tmp_path, _files())
    response = client.get(f"/api/repositories/{repo_id}/symbols", params={"limit": 500})
    assert response.status_code == 200, response.text
    return repo_id, {item["qualified_name"]: item for item in response.json()["items"]}


def _error(response) -> str:
    return response.json()["error"]["code"]


def test_cfg_dataflow_and_metrics_are_exact(client: TestClient, tmp_path):
    repo_id, symbols = _setup(client, tmp_path)
    compute = symbols["pkg.service.compute"]

    cfg_response = client.get(f"/api/symbols/{compute['id']}/cfg")
    assert cfg_response.status_code == 200, cfg_response.text
    cfg = cfg_response.json()
    assert cfg["symbol_id"] == compute["id"]
    assert cfg["qualified_name"] == "pkg.service.compute"
    assert cfg["rel_path"] == "pkg/service.py"
    assert cfg["start_line"] == 3
    assert cfg["end_line"] == 13
    assert [node["kind"] for node in cfg["nodes"]] == [
        "entry",
        "exit",
        "statement",
        "loop",
        "merge",
        "condition",
        "continue",
        "merge",
        "statement",
        "try",
        "statement",
        "handler",
        "statement",
        "merge",
        "return",
    ]
    assert [(edge["source"], edge["target"], edge["type"]) for edge in cfg["edges"]] == [
        ("n0", "n2", "normal"),
        ("n2", "n3", "normal"),
        ("n3", "n5", "loop_body"),
        ("n5", "n6", "true"),
        ("n6", "n3", "continue"),
        ("n5", "n7", "false"),
        ("n7", "n8", "normal"),
        ("n8", "n3", "loop_back"),
        ("n3", "n4", "loop_exit"),
        ("n4", "n9", "normal"),
        ("n9", "n10", "normal"),
        ("n9", "n11", "exception"),
        ("n10", "n11", "exception"),
        ("n11", "n12", "normal"),
        ("n10", "n13", "normal"),
        ("n12", "n13", "normal"),
        ("n13", "n14", "normal"),
        ("n14", "n1", "return"),
    ]
    assert Counter(edge["type"] for edge in cfg["edges"]) == {
        "normal": 9,
        "loop_body": 1,
        "true": 1,
        "continue": 1,
        "false": 1,
        "loop_back": 1,
        "loop_exit": 1,
        "exception": 2,
        "return": 1,
    }
    assert cfg["truncated"] is False
    assert cfg["source_changed_since_analysis"] is False

    dataflow_response = client.get(f"/api/symbols/{compute['id']}/dataflow")
    assert dataflow_response.status_code == 200, dataflow_response.text
    dataflow = dataflow_response.json()
    assert [(d["id"], d["name"], d["kind"], d["line"]) for d in dataflow["definitions"]] == [
        ("d0", "flag", "parameter", 3),
        ("d1", "total", "assignment", 4),
        ("d2", "i", "loop_target", 5),
        ("d3", "total", "assignment", 8),
        ("d4", "total", "assignment", 10),
        ("d5", "total", "assignment", 12),
    ]
    assert [(u["id"], u["name"], u["line"]) for u in dataflow["uses"]] == [
        ("u0", "range", 5),
        ("u1", "i", 6),
        ("u2", "flag", 6),
        ("u3", "helper", 8),
        ("u4", "total", 8),
        ("u5", "i", 8),
        ("u6", "total", 10),
        ("u7", "ZeroDivisionError", 11),
        ("u8", "total", 13),
    ]
    assert dataflow["undefined_uses"] == ["u0", "u3", "u7"]
    reaching = {item["use_id"]: item["definitions"] for item in dataflow["reaching"]}
    assert reaching["u4"] == ["d1", "d3"]
    assert reaching["u8"] == ["d4", "d5"]
    assert dataflow["truncated"] is False
    assert any("not claimed" in note for note in dataflow["notes"])

    metrics_response = client.get(
        f"/api/repositories/{repo_id}/metrics/functions", params={"limit": 500}
    )
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics["page"]["total"] == 6
    by_name = {item["qualified_name"]: item for item in metrics["items"]}
    assert set(by_name) == {
        "pkg.service.compute",
        "pkg.main.run",
        "pkg.util.helper",
        "pkg.util.unused",
        "cycle_a.a",
        "cycle_b.b",
    }
    compute_metrics = by_name["pkg.service.compute"]
    assert compute_metrics["symbol_id"] == compute["id"]
    assert compute_metrics["cyclomatic_complexity"] == 4
    assert compute_metrics["line_count"] == 11
    assert compute_metrics["call_count"] == 2
    assert compute_metrics["if_count"] == 1
    assert compute_metrics["for_count"] == 1
    assert compute_metrics["try_count"] == 1
    assert compute_metrics["return_count"] == 1
    assert compute_metrics["max_nesting_depth"] == 2

    filtered = client.get(
        f"/api/repositories/{repo_id}/metrics/functions",
        params={"symbol_id": compute["id"]},
    )
    assert filtered.status_code == 200
    assert filtered.json()["page"]["total"] == 1
    assert filtered.json()["items"][0]["symbol_id"] == compute["id"]
    empty = client.get(
        f"/api/repositories/{repo_id}/metrics/functions", params={"symbol_id": "missing"}
    )
    assert empty.status_code == 200
    assert empty.json()["page"]["total"] == 0

    # Phase 1 endpoints remain intact.
    for endpoint, total in [("files", 8), ("symbols", 13), ("relationships", 9)]:
        response = client.get(f"/api/repositories/{repo_id}/{endpoint}", params={"limit": 500})
        assert response.status_code == 200, (endpoint, response.text)
        assert response.json()["page"]["total"] == total, endpoint


def test_explanations_at_every_level(client: TestClient, tmp_path):
    repo_id, symbols = _setup(client, tmp_path)
    compute = symbols["pkg.service.compute"]

    function = client.get(f"/api/repositories/{repo_id}/explanations/function/{compute['id']}")
    assert function.status_code == 200, function.text
    body = function.json()
    assert body["level"] == "function"
    assert body["subject"] == "pkg.service.compute"
    assert any("Metrics: 11 lines" in fact for fact in body["facts"])
    assert any("Control-flow graph: 15 node(s), 18 edge(s)" in fact for fact in body["facts"])
    assert any("Local data flow: 6 definition(s), 9 use(s)" in fact for fact in body["facts"])
    assert any("Outgoing calls: 2 reference(s)" in fact for fact in body["facts"])
    assert any("lambda bodies and comprehension interiors" in item for item in body["unknowns"])
    evidence_kinds = {item["kind"] for item in body["evidence"]}
    assert {"symbol", "metric", "cfg", "relationship"} <= evidence_kinds
    assert all(item["rel_path"] in {"pkg/service.py", "pkg/main.py"} for item in body["evidence"])
    assert [section["title"] for section in body["sections"]] == [
        "Signature",
        "Structural metrics",
        "Control flow",
    ]
    assert any("condition:" in line for line in body["sections"][-1]["lines"])

    statement = client.get(
        f"/api/repositories/{repo_id}/explanations/statement/{compute['id']}",
        params={"line": 6},
    )
    assert statement.status_code == 200, statement.text
    statement_body = statement.json()
    assert statement_body["level"] == "statement"
    assert statement_body["title"] == "Statement at pkg/service.py:6"
    assert any("`condition` statement" in fact for fact in statement_body["facts"])
    assert any("loop_body" in fact for fact in statement_body["facts"])
    assert statement_body["sections"][0]["title"] == "Source"
    assert statement_body["evidence"][-1]["kind"] == "cfg"
    assert statement_body["evidence"][-1]["line"] == 6

    file_explanation = client.get(f"/api/repositories/{repo_id}/explanations/file/pkg/service.py")
    assert file_explanation.status_code == 200, file_explanation.text
    file_body = file_explanation.json()
    assert file_body["level"] == "file"
    assert any(
        "Imports: 1 statement reference(s) — 1 resolved_static." in f for f in file_body["facts"]
    )
    assert any("Resolved outgoing dependencies: `pkg/util.py`" in f for f in file_body["facts"])
    assert any("Resolved inbound dependents: `pkg/main.py`" in f for f in file_body["facts"])
    assert any("Entry point: no explicit" in f for f in file_body["facts"])
    assert file_body["unknowns"] == []

    folder = client.get(
        f"/api/repositories/{repo_id}/explanations/folder", params={"prefix": "pkg"}
    )
    assert folder.status_code == 200, folder.text
    folder_body = folder.json()
    assert folder_body["level"] == "folder"
    assert "Folder `pkg` contains 4 analyzed file(s)." in folder_body["facts"]
    assert "Internal resolved dependency edges: 2." in folder_body["facts"]
    assert "Import cycles entirely inside the folder: none detected." in folder_body["facts"]
    assert any("pkg.service.compute" in fact for fact in folder_body["facts"])

    project = client.get(f"/api/repositories/{repo_id}/explanations/project")
    assert project.status_code == 200, project.text
    project_body = project.json()
    assert project_body["level"] == "project"
    assert any("contains 8 analyzed file(s) and 13 symbol(s)" in f for f in project_body["facts"])
    assert any("Import cycles detected: 1." in f for f in project_body["facts"])
    assert any("pkg/main.py:6" in f for f in project_body["facts"])
    assert any("Architecture findings: 3." in f for f in project_body["facts"])
    assert [section["title"] for section in project_body["sections"]] == [
        "Architecture findings",
        "Thresholds",
    ]

    broken = client.get(f"/api/repositories/{repo_id}/explanations/file/broken.py")
    assert broken.status_code == 200, broken.text
    assert any("did not parse successfully" in item for item in broken.json()["unknowns"])
    assert broken.json()["sections"] == []

    empty = client.get(f"/api/repositories/{repo_id}/explanations/file/empty.py")
    assert empty.status_code == 200, empty.text
    assert any("Symbols: 1 module." in fact for fact in empty.json()["facts"])

    missing = client.get(f"/api/repositories/{repo_id}/explanations/file/nope.py")
    assert missing.status_code == 404
    assert _error(missing) == "file_not_found"

    no_run = make_repo(tmp_path, {"only.py": "x = 1\n"}, name="no_run")
    registered = client.post("/api/repositories", json={"name": "no_run", "path": str(no_run)})
    assert registered.status_code == 201
    no_run_id = registered.json()["id"]
    not_analyzed = client.get(f"/api/repositories/{no_run_id}/explanations/project")
    assert not_analyzed.status_code == 404
    assert _error(not_analyzed) == "analysis_not_available"

    unknown = client.get("/api/repositories/repo_missing/explanations/project")
    assert unknown.status_code == 404
    assert _error(unknown) == "repository_not_found"


def test_search_modes_filters_pagination_and_errors(client: TestClient, tmp_path):
    repo_id, _symbols = _setup(client, tmp_path)

    response = client.get(f"/api/repositories/{repo_id}/search", params={"q": "helper"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"] == {"total": 3, "limit": 50, "offset": 0}
    assert [
        (item["kind"], item["qualified_name"], item["line"], item["score"])
        for item in body["items"]
    ] == [
        ("symbol", "pkg.util.helper", 1, 100),
        ("text", "pkg.service", 1, 45),
        ("text", "pkg.service.compute", 8, 45),
    ]
    assert body["truncated"] is False

    symbol_mode = client.get(
        f"/api/repositories/{repo_id}/search", params={"q": "compute", "mode": "symbol"}
    )
    assert symbol_mode.status_code == 200
    assert symbol_mode.json()["page"]["total"] == 1
    assert symbol_mode.json()["items"][0]["qualified_name"] == "pkg.service.compute"

    identifier_mode = client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "pkg.service.compute", "mode": "identifier"},
    )
    assert identifier_mode.status_code == 200
    assert identifier_mode.json()["page"]["total"] == 1
    assert identifier_mode.json()["items"][0]["score"] == 75

    token_mode = client.get(
        f"/api/repositories/{repo_id}/search", params={"q": "total helper", "mode": "token"}
    )
    assert token_mode.status_code == 200
    assert token_mode.json()["page"]["total"] == 1
    assert token_mode.json()["items"][0]["line"] == 8
    assert token_mode.json()["items"][0]["kind"] == "text"

    text_mode = client.get(
        f"/api/repositories/{repo_id}/search", params={"q": "compute", "mode": "text"}
    )
    assert text_mode.status_code == 200
    assert text_mode.json()["page"]["total"] >= 2
    assert all(item["kind"] == "text" for item in text_mode.json()["items"])

    kind_filter = client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "helper", "kind": "function"},
    )
    assert kind_filter.status_code == 200
    assert kind_filter.json()["page"]["total"] == 2
    assert all(item["symbol_kind"] == "function" for item in kind_filter.json()["items"])

    path_filter = client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "helper", "path_prefix": "pkg/util.py"},
    )
    assert path_filter.status_code == 200
    assert path_filter.json()["page"]["total"] == 1
    assert path_filter.json()["items"][0]["qualified_name"] == "pkg.util.helper"

    page = client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "helper", "limit": 1, "offset": 1},
    )
    assert page.status_code == 200
    assert page.json()["page"] == {"total": 3, "limit": 1, "offset": 1}
    assert len(page.json()["items"]) == 1

    none = client.get(f"/api/repositories/{repo_id}/search", params={"q": "zzzz"})
    assert none.status_code == 200
    assert none.json()["items"] == []
    assert none.json()["page"]["total"] == 0
    assert none.json()["truncated"] is False

    blank = client.get(f"/api/repositories/{repo_id}/search", params={"q": "   "})
    assert blank.status_code == 400
    assert _error(blank) == "invalid_request"

    bad_identifier = client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "not an identifier", "mode": "identifier"},
    )
    assert bad_identifier.status_code == 400
    assert _error(bad_identifier) == "invalid_request"

    missing_query = client.get(f"/api/repositories/{repo_id}/search")
    assert missing_query.status_code == 422

    unknown = client.get("/api/repositories/repo_missing/search", params={"q": "x"})
    assert unknown.status_code == 404
    assert _error(unknown) == "repository_not_found"


def test_trace_connect_dependents_and_errors(client: TestClient, tmp_path):
    repo_id, symbols = _setup(client, tmp_path)
    run = symbols["pkg.main.run"]
    helper = symbols["pkg.util.helper"]
    compute = symbols["pkg.service.compute"]
    unused = symbols["pkg.util.unused"]

    out = client.get(
        f"/api/repositories/{repo_id}/trace",
        params={"symbol_id": run["id"], "direction": "out", "rel_types": "calls"},
    )
    assert out.status_code == 200, out.text
    body = out.json()
    assert [(node["qualified_name"], node["depth"]) for node in body["nodes"]] == [
        ("pkg.main.run", 0),
        ("pkg.service.compute", 1),
        ("pkg.util.helper", 2),
    ]
    assert [
        (edge["source_symbol_id"], edge["target_symbol_id"], edge["rel_type"])
        for edge in body["edges"]
    ] == [
        (run["id"], compute["id"], "calls"),
        (compute["id"], helper["id"], "calls"),
    ]
    assert [(edge["target_ref"], edge["resolution_status"]) for edge in body["unresolved"]] == [
        ("builtin:range", "external")
    ]
    assert body["cycles"] == []
    assert body["max_depth"] == 4
    assert body["max_nodes"] == 500
    assert any("not a runtime execution path" in note for note in body["notes"])

    reverse = client.get(
        f"/api/repositories/{repo_id}/trace",
        params={"symbol_id": helper["id"], "direction": "in", "rel_types": "calls"},
    )
    assert reverse.status_code == 200, reverse.text
    assert [(node["qualified_name"], node["depth"]) for node in reverse.json()["nodes"]] == [
        ("pkg.util.helper", 0),
        ("pkg.service.compute", 1),
        ("pkg.main.run", 2),
        ("pkg.main", 3),
    ]

    shallow = client.get(
        f"/api/repositories/{repo_id}/trace",
        params={"symbol_id": run["id"], "direction": "out", "max_depth": 1},
    )
    assert shallow.status_code == 200
    assert [node["qualified_name"] for node in shallow.json()["nodes"]] == [
        "pkg.main.run",
        "pkg.service.compute",
    ]

    capped = client.get(
        f"/api/repositories/{repo_id}/trace",
        params={"symbol_id": run["id"], "direction": "out", "max_nodes": 2},
    )
    assert capped.status_code == 200
    assert [node["qualified_name"] for node in capped.json()["nodes"]] == [
        "pkg.main.run",
        "pkg.service.compute",
    ]
    assert capped.json()["truncated"] is True

    connect = client.get(
        f"/api/repositories/{repo_id}/trace/connect",
        params={"source_symbol_id": run["id"], "target_symbol_id": helper["id"]},
    )
    assert connect.status_code == 200, connect.text
    assert connect.json()["found"] is True
    assert connect.json()["nodes"] == [run["id"], compute["id"], helper["id"]]
    assert connect.json()["length"] == 2

    no_path = client.get(
        f"/api/repositories/{repo_id}/trace/connect",
        params={"source_symbol_id": run["id"], "target_symbol_id": unused["id"]},
    )
    assert no_path.status_code == 200
    assert no_path.json()["found"] is False
    assert "no static relationship path" in no_path.json()["reason"]

    dependents = client.get(
        f"/api/repositories/{repo_id}/dependents", params={"rel_path": "pkg/util.py"}
    )
    assert dependents.status_code == 200, dependents.text
    assert [(node["qualified_name"], node["depth"]) for node in dependents.json()["nodes"]] == [
        ("pkg.util", 0),
        ("pkg.service", 1),
        ("pkg.main", 2),
    ]

    broken_dependents = client.get(
        f"/api/repositories/{repo_id}/dependents", params={"rel_path": "broken.py"}
    )
    assert broken_dependents.status_code == 404
    assert _error(broken_dependents) == "file_not_found"

    unknown_symbol = client.get(
        f"/api/repositories/{repo_id}/trace", params={"symbol_id": "missing"}
    )
    assert unknown_symbol.status_code == 404
    assert _error(unknown_symbol) == "symbol_not_found"

    bad_type = client.get(
        f"/api/repositories/{repo_id}/trace",
        params={"symbol_id": run["id"], "rel_types": "calls,nope"},
    )
    assert bad_type.status_code == 400
    assert _error(bad_type) == "invalid_request"

    unknown_repo = client.get(
        "/api/repositories/repo_missing/trace", params={"symbol_id": run["id"]}
    )
    assert unknown_repo.status_code == 404
    assert _error(unknown_repo) == "repository_not_found"


def test_architecture_summary_and_findings_api(client: TestClient, tmp_path):
    repo_id, _symbols = _setup(client, tmp_path)

    response = client.get(f"/api/repositories/{repo_id}/architecture")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["files"] == 8
    assert body["dependency_edges"] == 4
    assert body["disconnected_components"] == 5
    assert body["truncated"] is False
    assert len(body["cycles"]) == 1
    assert body["cycles"][0]["files"] == ["cycle_a.py", "cycle_b.py", "cycle_a.py"]
    assert [
        (edge["source_rel_path"], edge["target_rel_path"], edge["line"])
        for edge in body["cycles"][0]["edges"]
    ] == [("cycle_a.py", "cycle_b.py", 1), ("cycle_b.py", "cycle_a.py", 1)]
    assert body["entry_points"] == [
        {
            "rel_path": "pkg/main.py",
            "line": 6,
            "evidence": "explicit if __name__ == '__main__' guard",
        }
    ]
    assert [
        (item["rel_path"], item["in_degree"], item["out_degree"], item["centrality"])
        for item in body["central_components"][:3]
    ] == [
        ("cycle_a.py", 1, 1, 0.285714),
        ("cycle_b.py", 1, 1, 0.285714),
        ("pkg/service.py", 1, 1, 0.285714),
    ]
    service = next(item for item in body["coupling"] if item["rel_path"] == "pkg/service.py")
    assert service == {
        "rel_path": "pkg/service.py",
        "fan_in": 1,
        "fan_out": 1,
        "instability": 0.5,
    }
    assert body["complexity_hotspots"][0]["qualified_name"] == "pkg.service.compute"
    assert body["complexity_hotspots"][0]["cyclomatic_complexity"] == 4
    assert [finding["kind"] for finding in body["findings"]] == [
        "dependency_cycle",
        "central_component",
        "disconnected_components",
    ]
    cycle_finding, central_finding, disconnected = body["findings"]
    assert cycle_finding["heuristic"] is False
    assert cycle_finding["severity"] == "warning"
    assert cycle_finding["metrics"] == {"length": 2}
    assert central_finding["heuristic"] is True
    assert central_finding["subject"] == "cycle_a.py"
    assert disconnected["heuristic"] is False
    assert disconnected["metrics"] == {"components": 5}
    assert body["thresholds"]["complexity_warning"] == 10
    assert "degree centrality" in body["thresholds"]["centrality"]
    assert any("calls are not treated as module dependencies" in note for note in body["notes"])

    findings = client.get(f"/api/repositories/{repo_id}/architecture/findings")
    assert findings.status_code == 200
    assert findings.json()["page"]["total"] == 3

    page = client.get(
        f"/api/repositories/{repo_id}/architecture/findings",
        params={"limit": 1, "offset": 1},
    )
    assert page.status_code == 200
    assert page.json()["page"] == {"total": 3, "limit": 1, "offset": 1}
    assert page.json()["items"][0]["kind"] == "central_component"

    cycles_only = client.get(
        f"/api/repositories/{repo_id}/architecture/findings",
        params={"kind": "dependency_cycle"},
    )
    assert cycles_only.status_code == 200
    assert cycles_only.json()["page"]["total"] == 1
    assert cycles_only.json()["items"][0]["heuristic"] is False

    warnings = client.get(
        f"/api/repositories/{repo_id}/architecture/findings",
        params={"severity": "warning"},
    )
    assert warnings.status_code == 200
    assert warnings.json()["page"]["total"] == 1

    none = client.get(
        f"/api/repositories/{repo_id}/architecture/findings",
        params={"kind": "missing"},
    )
    assert none.status_code == 200
    assert none.json()["page"]["total"] == 0

    invalid_limit = client.get(
        f"/api/repositories/{repo_id}/architecture/findings", params={"limit": 0}
    )
    assert invalid_limit.status_code == 422

    unknown = client.get("/api/repositories/repo_missing/architecture")
    assert unknown.status_code == 404
    assert _error(unknown) == "repository_not_found"


def test_source_change_and_unavailable_are_reported(client: TestClient, tmp_path):
    repo = make_repo(tmp_path, {"m.py": "def f(a):\n    return a\n"})
    registered = client.post("/api/repositories", json={"name": "src", "path": str(repo)})
    assert registered.status_code == 201
    repo_id = registered.json()["id"]
    analyze(client, repo_id)
    symbol_id = symbol_id_for(client, repo_id, "m.f")

    (repo / "m.py").write_text("# changed\ndef f(a):\n    return a + 1\n", encoding="utf-8")
    changed = client.get(f"/api/symbols/{symbol_id}/cfg")
    assert changed.status_code == 200, changed.text
    assert changed.json()["source_changed_since_analysis"] is True
    assert changed.json()["start_line"] == 2
    changed_flow = client.get(f"/api/symbols/{symbol_id}/dataflow")
    assert changed_flow.status_code == 200, changed_flow.text
    assert changed_flow.json()["source_changed_since_analysis"] is True
    explanation = client.get(f"/api/repositories/{repo_id}/explanations/function/{symbol_id}")
    assert explanation.status_code == 200
    assert any(
        "changed after the stored analysis" in item for item in explanation.json()["unknowns"]
    )

    (repo / "m.py").unlink()
    unavailable = client.get(f"/api/symbols/{symbol_id}/cfg")
    assert unavailable.status_code == 409
    assert _error(unavailable) == "source_unavailable"
    file_explanation = client.get(f"/api/repositories/{repo_id}/explanations/file/m.py")
    assert file_explanation.status_code == 200
    assert any(
        "Current source is unavailable" in item for item in file_explanation.json()["unknowns"]
    )


def test_reanalysis_cleans_stale_symbols_and_metrics(client: TestClient, tmp_path):
    repo_id, symbols = _setup(client, tmp_path)
    old_compute = symbols["pkg.service.compute"]
    assert old_compute["id"]

    repo = tmp_path / "repo"
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

    stale = client.get(f"/api/symbols/{old_compute['id']}/cfg")
    assert stale.status_code == 404
    assert _error(stale) == "symbol_not_found"

    stale_metrics = client.get(
        f"/api/repositories/{repo_id}/metrics/functions",
        params={"symbol_id": old_compute["id"]},
    )
    assert stale_metrics.status_code == 200
    assert stale_metrics.json()["page"]["total"] == 0

    metrics = client.get(
        f"/api/repositories/{repo_id}/metrics/functions", params={"limit": 500}
    ).json()
    by_name = {item["qualified_name"]: item for item in metrics["items"]}
    assert "pkg.service.compute" not in by_name
    assert "pkg.service.compute_new" in by_name
    assert by_name["pkg.service.compute_new"]["cyclomatic_complexity"] == 2
    assert by_name["pkg.service.compute_new"]["symbol_id"]

    search = client.get(
        f"/api/repositories/{repo_id}/search",
        params={"q": "compute", "mode": "symbol"},
    )
    assert search.status_code == 200
    assert [item["qualified_name"] for item in search.json()["items"]] == [
        "pkg.service.compute_new"
    ]

    architecture = client.get(f"/api/repositories/{repo_id}/architecture").json()
    assert architecture["complexity_hotspots"][0]["qualified_name"] == "pkg.service.compute_new"


def test_folder_explanation_reports_outgoing_imports_outside_the_folder(
    client: TestClient, tmp_path
):
    repo_id = analyzed_temp_repo(
        client,
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/inside.py": ("from ext import helper\n\ndef use():\n    return helper()\n"),
            "ext.py": "def helper():\n    return 1\n",
        },
    )
    response = client.get(
        f"/api/repositories/{repo_id}/explanations/folder", params={"prefix": "pkg"}
    )
    assert response.status_code == 200, response.text
    facts = response.json()["facts"]
    assert "Outgoing cross-boundary dependencies: 1." in facts
    assert any("Outgoing targets: `ext.py`." in fact for fact in facts)


def test_function_and_statement_explanations_require_a_completed_run(client: TestClient, tmp_path):
    repo = make_repo(
        tmp_path,
        {"m.py": "def f():\n    return 1\n"},
        name="not_analyzed",
    )
    response = client.post("/api/repositories", json={"name": "not_analyzed", "path": str(repo)})
    assert response.status_code == 201
    repo_id = response.json()["id"]

    for path in (
        f"/api/repositories/{repo_id}/explanations/function/sym_missing",
        f"/api/repositories/{repo_id}/explanations/statement/sym_missing?line=1",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert _error(response) == "analysis_not_available"

    unknown = client.get("/api/repositories/repo_missing/explanations/function/sym_missing")
    assert unknown.status_code == 404
    assert _error(unknown) == "repository_not_found"


def test_cross_repository_symbols_do_not_leak(client: TestClient, tmp_path):
    repo_one = make_repo(tmp_path, {"alpha.py": "def unique_alpha():\n    return 1\n"}, name="r1")
    repo_two = make_repo(tmp_path, {"beta.py": "def unique_beta():\n    return 2\n"}, name="r2")
    r1 = client.post("/api/repositories", json={"name": "r1", "path": str(repo_one)}).json()["id"]
    r2 = client.post("/api/repositories", json={"name": "r2", "path": str(repo_two)}).json()["id"]
    analyze(client, r1)
    analyze(client, r2)
    alpha_id = symbol_id_for(client, r1, "alpha.unique_alpha")

    cross_explanation = client.get(f"/api/repositories/{r2}/explanations/function/{alpha_id}")
    assert cross_explanation.status_code == 400
    assert _error(cross_explanation) == "invalid_request"

    cross_trace = client.get(f"/api/repositories/{r2}/trace", params={"symbol_id": alpha_id})
    assert cross_trace.status_code == 404
    assert _error(cross_trace) == "symbol_not_found"

    search = client.get(f"/api/repositories/{r2}/search", params={"q": "unique_alpha"})
    assert search.status_code == 200
    assert search.json()["page"]["total"] == 0

    architecture = client.get(f"/api/repositories/{r2}/architecture").json()
    assert {item["rel_path"] for item in architecture["central_components"]} == {"beta.py"}
    metrics = client.get(f"/api/repositories/{r2}/metrics/functions", params={"limit": 500}).json()
    assert [item["qualified_name"] for item in metrics["items"]] == ["beta.unique_beta"]
