"""T14: bounded graph queries (neighbors, filters, paths, stats)."""

from __future__ import annotations

from repolens.analysis.pipeline import AnalysisPipeline
from repolens.config import Settings
from repolens.core.models import Direction, GraphNodeType, SymbolKind
from repolens.graph.builder import GraphBuilder
from repolens.graph.queries import GraphQuery
from tests.conftest import fixture_repo


def _setup(name: str):
    outcome = AnalysisPipeline(Settings()).analyze(f"repo_{name}", fixture_repo(name))
    symbols = [s for f in outcome.files for s in f.symbols]
    graph = GraphBuilder(f"repo_{name}", outcome.files, symbols, outcome.relationships).build()
    return outcome, GraphQuery(graph, max_depth=6, max_visited=20000)


def _symbol_id(outcome, qualified: str, kind: SymbolKind) -> str:
    for f in outcome.files:
        for s in f.symbols:
            if s.qualified_name == qualified and s.kind is kind:
                return s.symbol_id
    raise AssertionError(f"symbol not found: {qualified}")


def test_nodes_type_filter():
    _, query = _setup("b")
    files, total = query.nodes(node_type="file", limit=100, offset=0)
    assert total == 3
    assert all(n.type is GraphNodeType.FILE for n in files)


def test_neighbors_direction_and_type():
    outcome, query = _setup("c")
    cls = _symbol_id(outcome, "core.Animal", SymbolKind.CLASS)
    _, out_nodes, out_edges = query.neighbors(cls, direction=Direction.OUT)
    assert [n.label for n in out_nodes] == ["core.Animal.speak"]
    assert all(e.type == "contains" for e in out_edges)

    _, in_nodes, _ = query.neighbors(cls, direction=Direction.IN)
    assert any(n.type is GraphNodeType.FILE for n in in_nodes)


def test_neighbors_type_filter_excludes_other_types():
    outcome, query = _setup("c")
    cls = _symbol_id(outcome, "core.Animal", SymbolKind.CLASS)
    _, nodes, _ = query.neighbors(cls, edge_type="calls", direction=Direction.BOTH)
    assert nodes == []


def test_path_found_within_contains_chain():
    outcome, query = _setup("c")
    file_id = outcome.files[0].file_id
    method = _symbol_id(outcome, "core.Animal.speak", SymbolKind.METHOD)
    result = query.path(file_id, method)
    assert result.found
    assert result.nodes[0] == file_id and result.nodes[-1] == method
    assert result.length == len(result.nodes) - 1


def test_path_respects_max_depth():
    outcome, query = _setup("c")
    file_id = outcome.files[0].file_id
    method = _symbol_id(outcome, "core.Animal.speak", SymbolKind.METHOD)
    result = query.path(file_id, method, max_depth=1)
    assert not result.found
    assert "max_depth" in (result.reason or "")


def test_path_unknown_nodes_reports_found_false():
    _, query = _setup("c")
    result = query.path("nope", "also-nope")
    assert not result.found
    assert result.reason


def test_stats_counts_include_contains_edges():
    _, query = _setup("b")
    stats = query.stats("repo_b")
    assert stats.nodes > 0 and stats.edges > 0
    assert stats.by_node_type.get("file") == 3
    assert stats.by_edge_type.get("contains", 0) > 0
