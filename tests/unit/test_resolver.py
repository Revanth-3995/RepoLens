"""T12: conservative resolution, explicit statuses, unresolved preserved."""

from __future__ import annotations

from repolens.analysis.pipeline import AnalysisPipeline
from repolens.config import Settings
from repolens.core.models import RelationshipType, ResolutionStatus
from tests.conftest import fixture_repo


def _outcome(name: str):
    return AnalysisPipeline(Settings()).analyze(f"repo_{name}", fixture_repo(name))


def _rels(outcome, rel_type: RelationshipType):
    return [r for r in outcome.relationships if r.rel_type is rel_type]


def test_internal_call_resolved_statically():
    outcome = _outcome("a")
    calls = _rels(outcome, RelationshipType.CALLS)
    resolved = [c for c in calls if c.resolution_status is ResolutionStatus.RESOLVED_STATIC]
    assert any(c.target_ref == "simple.add" for c in resolved)
    for rel in resolved:
        assert rel.target_symbol_id  # resolved implies a concrete in-repo target


def test_external_imports_are_external_not_resolved():
    outcome = _outcome("d")
    imports = _rels(outcome, RelationshipType.IMPORTS)
    by_ref = {i.target_ref: i for i in imports}
    assert by_ref["os"].resolution_status is ResolutionStatus.EXTERNAL
    assert by_ref["unknown_package"].resolution_status is ResolutionStatus.EXTERNAL
    for rel in imports:
        if rel.resolution_status is not ResolutionStatus.RESOLVED_STATIC:
            assert rel.target_symbol_id is None


def test_relative_import_without_target_is_unresolved():
    outcome = _outcome("d")
    imports = _rels(outcome, RelationshipType.IMPORTS)
    unresolved = [i for i in imports if i.resolution_status is ResolutionStatus.UNRESOLVED]
    assert unresolved  # `from . import missing_sibling` in a root module


def test_dynamic_attribute_and_name_calls_marked_dynamic():
    outcome = _outcome("d")
    calls = _rels(outcome, RelationshipType.CALLS)
    dynamic = {
        c.call_expr for c in calls if c.resolution_status is ResolutionStatus.DYNAMIC_OR_UNKNOWN
    }
    assert "obj.method" in dynamic
    assert "fn" in dynamic


def test_builtins_marked_external_with_builtin_ref():
    outcome = _outcome("a")
    calls = _rels(outcome, RelationshipType.CALLS)
    builtin_refs = {c.target_ref for c in calls if c.target_ref.startswith("builtin:")}
    assert "builtin:len" in builtin_refs


def test_inheritance_resolved_within_repo():
    outcome = _outcome("c")
    inherits = _rels(outcome, RelationshipType.INHERITS_FROM)
    assert inherits
    assert all(r.resolution_status is ResolutionStatus.RESOLVED_STATIC for r in inherits)
    assert all(r.target_symbol_id for r in inherits)


def test_resolved_requires_in_repo_target():
    """No relationship may be 'resolved_static' while lacking an in-repo target id."""
    for name in ("a", "b", "c", "d"):
        outcome = _outcome(name)
        for rel in outcome.relationships:
            if rel.resolution_status is ResolutionStatus.RESOLVED_STATIC:
                assert rel.target_symbol_id, f"{name}:{rel.rel_type}:{rel.target_ref}"


def test_relationship_ids_are_unique_within_a_run():
    for name in ("a", "b", "c", "d"):
        outcome = _outcome(name)
        ids = [r.relationship_id for r in outcome.relationships]
        assert len(ids) == len(set(ids))
