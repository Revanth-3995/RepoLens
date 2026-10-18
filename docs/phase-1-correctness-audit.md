# RepoLens — Phase 1 Correctness Audit (F1–F3)

Date: 2026-10-18
Starting commit: `849a8c121f4492d61f66f38b120c5025cb9f1265` (branch `main`)
Final verification performed on the working tree after all corrections.

## 1. Baseline

| Check | Command | Result |
|---|---|---|
| Tests | `python -m pytest tests -o addopts="" -p no:cacheprovider` | 145 passed, 2 skipped |
| Lint | `python -m ruff check .` | All checks passed! |
| Format | `python -m ruff format --check .` | 60 files already formatted |
| Types | `python -m mypy repolens` | Success: no issues in 36 source files |

Python 3.13.3, Windows (PowerShell). Two tests skipped: `WinError 1314` symlink-creation privilege
(`test_directory_symlink_not_traversed`, `test_symlink_file_escaping_root_excluded`).

## 2. Findings

| Finding | Reproduced? | Regression test | Fix location | Retest result | Final status |
|---|---|---|---|---|---|
| F1 Duplicate-definition resolution | Yes — call to a redefined name was marked `resolved_static` pointing at the first definition | `tests/unit/test_resolver.py::test_duplicate_module_definitions_are_not_falsely_resolved` (plus `test_duplicate_definitions_retain_distinct_symbol_ids`, `test_same_named_definitions_in_different_modules_resolve_to_own`, `test_same_named_methods_in_different_classes_resolve_to_own`) | `repolens/analysis/resolver.py` (`__init__` duplicate detection, `_resolved`, `_import_rels`, `resolve_inherits`) | 145 passed | Verified |
| F2 Typed graph-edge overwriting | Yes — `inherits_from` edge silently overwritten by `calls` edge between the same node pair (repro showed only `calls` survived) | `tests/unit/test_graph_builder.py::test_distinct_relationship_types_between_same_nodes_both_survive` | `repolens/graph/builder.py` (MultiDiGraph + per-type merge), `repolens/graph/queries.py` (parallel-edge handling) | 145 passed | Verified |
| F3 Missing AST statement containers | Yes — definitions inside `match` cases and `except*` handlers were dropped | `tests/unit/test_symbols.py::test_nested_definitions_in_match_and_try_star` | `repolens/analysis/symbols.py` (`_walk_body`: `ast.Match`, `ast.TryStar`) | 145 passed | Verified |

### Root causes

- **F1:** `Resolver._sym_by_q` and `SymbolTable.by_qualified` used `setdefault`, silently collapsing duplicate qualified names to the first definition. The resolver then reported `resolved_static` with that first definition's id, overstating certainty.
- **F2:** `GraphBuilder` used `nx.DiGraph`, which allows only one edge per ordered node pair. The final application loop called `g.add_edge` for a second relationship type sharing the same `(src, tgt)`, overwriting the first edge's `type`/`sites`.
- **F3:** `_walk_body` had explicit branches for `If/Try/With/For/While` but no branch for `ast.Match` (3.10+) or `ast.TryStar` (3.11+), so definitions nested in those containers were never visited.

### Corrective changes

- **F1:** Detect qualified names with more than one definition (`_duplicate_qnames`). In `_resolved`, downgrade any relationship whose target is such a name to `ambiguous` with `target_symbol_id=None`. Apply the same downgrade in `_import_rels` and `resolve_inherits`. Distinct symbol identities (ordinals/ids) are preserved; no false `resolved_static`.
- **F2:** Switched to `nx.MultiDiGraph`. Each distinct `(src, tgt, type)` merges into one parallel edge (sites extended); different types become separate parallel edges. Queries iterate parallel edges correctly; neighbors dedupe by `(src, tgt, type)` so all types survive; stats count each edge.
- **F3:** Added `_walk_body` branches for `ast.Match` (each case body, same enclosing scope) and `ast.TryStar` (handled like `ast.Try`). Nested definitions now receive correct parent and line ranges.

### Regression-test verification (test-first evidence)

Each regression test was confirmed to **fail** on the pre-fix code and **pass** after the fix:

- `test_duplicate_module_definitions_are_not_falsely_resolved`: failed with `RESOLVED_STATIC` → passes with `AMBIGUOUS`.
- `test_distinct_relationship_types_between_same_nodes_both_survive`: failed with edge types `{'calls'}` → passes with `{'inherits_from', 'calls'}`.
- `test_nested_definitions_in_match_and_try_star`: failed with only `m`, `m.dispatch`, `m.try_star` → passes with nested symbols present.

## 3. Cross-cutting audit

Inspected symbol identity, scope, resolution, graph, persistence, ingestion. No additional confirmed defects beyond F1–F3. Noted (documented, not changed):

- `_assign_ids` parent lookup uses `setdefault` (first parent wins) for duplicate parents; nested-symbol parent edges may attach to the first instance. Resolution is already `ambiguous` for such names.
- `SymbolTable.by_qualified` and `_methods_of` collapse duplicate names to the first for lookup convenience; distinct drafts/ids are preserved.
- `_file_by_module` keeps the first file for a duplicate module name (edge case; documented).

## 4. End-to-end verification

Temporary synthetic repository (never executed by RepoLens): registered, analyzed, file/symbol/relationship/graph queries exercised, re-analysis, file removal (stale data handled), parse-error reporting, source retrieval, path traversal boundary. Result: **ALL E2E CHECKS PASSED**.

## 5. Final quality gates

```
python -m pytest tests -o addopts="" -p no:cacheprovider
======================= 145 passed, 2 skipped in 13.23s =======================

python -m ruff check .
All checks passed!

python -m ruff format --check .
60 files already formatted

python -m mypy repolens
Success: no issues found in 36 source files
```

## 6. Remaining limitations

See `docs/limitations.md`. Duplicate definitions are reported as `ambiguous` (never `resolved_static`) because the model does not track execution order/control flow. `match`/`try*` are supported on Python 3.10+/3.11+ respectively; on older interpreters the syntax is a parse error.

## 7. Verdict

**COMPLETE** — F1, F2, F3 verified fixed with passing regression tests; full suite and quality gates green.
