"""T12: conservative resolution, explicit statuses, unresolved preserved."""

from __future__ import annotations

from pathlib import Path

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


def _write_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def test_duplicate_module_definitions_are_not_falsely_resolved(tmp_path: Path):
    root = _write_repo(
        tmp_path,
        {
            "m.py": (
                "def target():\n    return 'first'\n\n"
                "def target():\n    return 'second'\n\n"
                "def caller():\n    return target()\n"
            )
        },
    )
    outcome = AnalysisPipeline(Settings()).analyze("repo_dup", root)
    calls = [r for r in outcome.relationships if r.rel_type is RelationshipType.CALLS]
    assert calls, "expected a call relationship"
    # The duplicate name must not be reported as a certain resolved_static target.
    dup_calls = [c for c in calls if c.call_expr == "target"]
    assert dup_calls, "expected a call to the duplicated name"
    for c in dup_calls:
        assert c.resolution_status is ResolutionStatus.AMBIGUOUS
        assert c.target_symbol_id is None


def test_duplicate_definitions_retain_distinct_symbol_ids(tmp_path: Path):
    root = _write_repo(
        tmp_path,
        {"m.py": "def f():\n    return 1\n\ndef f():\n    return 2\n"},
    )
    outcome = AnalysisPipeline(Settings()).analyze("repo_dup2", root)
    symbols = [s for f in outcome.files for s in f.symbols if s.qualified_name == "m.f"]
    assert len(symbols) == 2
    assert len({s.symbol_id for s in symbols}) == 2


def test_same_named_definitions_in_different_modules_resolve_to_own(tmp_path: Path):
    root = _write_repo(
        tmp_path,
        {
            "a.py": "def target():\n    return 'a'\n\ndef caller():\n    return target()\n",
            "b.py": "def target():\n    return 'b'\n\ndef caller():\n    return target()\n",
        },
    )
    outcome = AnalysisPipeline(Settings()).analyze("repo_multi", root)
    calls = [r for r in outcome.relationships if r.rel_type is RelationshipType.CALLS]
    # Each call resolves to its own module's definition, never cross-wired.
    for c in calls:
        assert c.resolution_status is ResolutionStatus.RESOLVED_STATIC
        assert c.target_symbol_id is not None
        assert c.target_ref.startswith(("a.target", "b.target"))


def test_same_named_methods_in_different_classes_resolve_to_own(tmp_path: Path):
    root = _write_repo(
        tmp_path,
        {
            "m.py": (
                "class A:\n    def run(self):\n        return 1\n\n"
                "class B:\n    def run(self):\n        return 2\n\n"
                "def use(a, b):\n    return a.run() + b.run()\n"
            )
        },
    )
    outcome = AnalysisPipeline(Settings()).analyze("repo_cls", root)
    symbols = [s for f in outcome.files for s in f.symbols]
    runs = [s for s in symbols if s.name == "run"]
    assert len(runs) == 2
    assert len({s.symbol_id for s in runs}) == 2


def test_stable_nested_definition_call_is_resolved(tmp_path: Path):
    root = _write_repo(
        tmp_path,
        {
            "m.py": (
                "def outer(value):\n"
                "    def inner(x):\n"
                "        return x + 1\n"
                "    return inner(value)\n"
            )
        },
    )
    outcome = AnalysisPipeline(Settings()).analyze("repo_nested", root)
    symbols = [s for f in outcome.files for s in f.symbols]
    inner = next(s for s in symbols if s.qualified_name == "m.outer.inner")
    calls = [
        r
        for r in outcome.relationships
        if r.rel_type is RelationshipType.CALLS and r.call_expr == "inner"
    ]
    assert len(calls) == 1
    call = calls[0]
    assert call.resolution_status is ResolutionStatus.RESOLVED_STATIC
    assert call.target_ref == "m.outer.inner"
    assert call.target_symbol_id == inner.symbol_id
    assert call.evidence == "stable nested definition m.outer.inner"


def test_match_capture_rebinding_nested_definition_stays_unresolved(tmp_path: Path):
    root = _write_repo(
        tmp_path,
        {
            "m.py": (
                "def outer(value):\n"
                "    def inner():\n"
                "        return 1\n"
                "    match value:\n"
                "        case inner:\n"
                "            pass\n"
                "    return inner()\n"
            )
        },
    )
    outcome = AnalysisPipeline(Settings()).analyze("repo_match_rebind", root)
    calls = [
        r
        for r in outcome.relationships
        if r.rel_type is RelationshipType.CALLS and r.call_expr == "inner"
    ]
    assert len(calls) == 1
    assert calls[0].resolution_status is ResolutionStatus.DYNAMIC_OR_UNKNOWN
    assert calls[0].target_symbol_id is None
    assert "bound locally" in calls[0].evidence


def test_rebound_nested_definition_call_stays_dynamic(tmp_path: Path):
    root = _write_repo(
        tmp_path,
        {
            "m.py": (
                "def outer():\n"
                "    def inner():\n"
                "        return 1\n"
                "    inner = lambda: 2\n"
                "    return inner()\n"
            )
        },
    )
    outcome = AnalysisPipeline(Settings()).analyze("repo_rebound", root)
    calls = [
        r
        for r in outcome.relationships
        if r.rel_type is RelationshipType.CALLS and r.call_expr == "inner"
    ]
    assert len(calls) == 1
    assert calls[0].resolution_status is ResolutionStatus.DYNAMIC_OR_UNKNOWN
    assert calls[0].target_symbol_id is None
    assert "bound locally" in calls[0].evidence
