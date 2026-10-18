"""T13: graph nodes/edges consistent with extracted facts."""

from __future__ import annotations

import pytest

from repolens.analysis.pipeline import AnalysisPipeline
from repolens.config import Settings
from repolens.core.errors import GraphIntegrityError
from repolens.core.models import FileAnalysis, ParseStatus, SymbolDraft, SymbolKind
from repolens.graph.builder import GraphBuilder
from tests.conftest import fixture_repo


def _outcome(name: str):
    return AnalysisPipeline(Settings()).analyze(f"repo_{name}", fixture_repo(name))


def _graph(name: str):
    outcome = _outcome(name)
    symbols = [s for f in outcome.files for s in f.symbols]
    return outcome, GraphBuilder(
        f"repo_{name}", outcome.files, symbols, outcome.relationships
    ).build()


def test_repository_node_and_file_nodes_present():
    outcome, graph = _graph("b")
    repo_nodes = [n for n, d in graph.nodes(data=True) if d.get("type") == "repository"]
    assert repo_nodes == ["repo_repo_b"]
    file_nodes = {n for n, d in graph.nodes(data=True) if d.get("type") == "file"}
    assert file_nodes == {f.file_id for f in outcome.files}
    for f in outcome.files:
        assert graph.has_edge("repo_repo_b", f.file_id)


def test_module_symbols_are_not_separate_nodes():
    outcome, graph = _graph("b")
    module_ids = {
        s.symbol_id for f in outcome.files for s in f.symbols if s.kind is SymbolKind.MODULE
    }
    assert module_ids  # there are module symbols
    for module_id in module_ids:
        assert module_id not in graph


def test_class_and_method_containment_edges():
    _, graph = _graph("c")
    contains = {(u, v) for u, v, d in graph.edges(data=True) if d.get("type") == "contains"}
    assert contains
    for _, _, data in graph.edges(data=True):
        if data.get("type") == "contains":
            assert data.get("sites") == []


def test_symbols_are_contained_somewhere():
    _, graph = _graph("c")
    for nid, data in graph.nodes(data=True):
        if data.get("type") in {"class", "function", "method"}:
            assert any(
                any(
                    ed.get("type") == "contains"
                    for ed in (graph.get_edge_data(p, nid) or {}).values()
                )
                for p in graph.predecessors(nid)
            )


def test_only_resolved_static_relationships_become_edges():
    outcome, graph = _graph("d")
    edge_types = {d.get("type") for _, _, d in graph.edges(data=True)}
    assert edge_types <= {"contains", "imports", "calls", "inherits_from"}
    # 'calls' edges exist only for resolved calls; fixture D has none resolved.
    call_edges = [d for _, _, d in graph.edges(data=True) if d.get("type") == "calls"]
    assert call_edges == []


def test_sites_are_merged_for_duplicate_edges():
    outcome, graph = _graph("a")
    for _, _, data in graph.edges(data=True):
        if data.get("type") == "calls":
            assert data.get("sites")


def test_distinct_relationship_types_between_same_nodes_both_survive():
    """F2 regression: inherits_from + calls on the same node pair must not overwrite."""
    source = "class Base:\n    pass\n\nclass Child(Base):\n    Base()\n"
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    (d / "m.py").write_text(source, encoding="utf-8")
    from repolens.analysis.pipeline import AnalysisPipeline
    from repolens.config import Settings

    outcome = AnalysisPipeline(Settings()).analyze("repo_f2", d)
    symbols = [s for f in outcome.files for s in f.symbols]
    graph = GraphBuilder("repo_f2", outcome.files, symbols, outcome.relationships).build()
    child = next(s.symbol_id for s in symbols if s.name == "Child")
    base = next(s.symbol_id for s in symbols if s.name == "Base")
    edge_types = {
        data.get("type") for u, v, data in graph.edges(data=True) if u == child and v == base
    }
    assert "inherits_from" in edge_types
    assert "calls" in edge_types


def test_unknown_file_reference_fails_integrity():
    good = FileAnalysis(
        rel_path="a.py",
        module_name="a",
        size_bytes=1,
        content_hash="h",
        parse_status=ParseStatus.PARSED,
        file_id="file_a",
        symbols=[
            SymbolDraft(
                name="C",
                qualified_name="a.C",
                kind=SymbolKind.CLASS,
                ordinal=0,
                start_line=1,
                end_line=1,
                symbol_id="sym_c",
                file_id="file_missing",
            )
        ],
    )
    with pytest.raises(GraphIntegrityError):
        GraphBuilder("repo_x", [good], list(good.symbols), []).build()
