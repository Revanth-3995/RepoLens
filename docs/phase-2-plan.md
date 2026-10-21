# RepoLens — Phase 2 Plan: Code Comprehension Engine

This document is the authoritative Phase 2 work-package plan, status matrix, and
semantics reference. Phase 2 is **deterministic and evidence-backed**. It adds no
LLMs, generative APIs, hosted AI, embeddings, model training, or cosmetic ML.
Every statement, trace, explanation, metric, and finding is derived from parsed
source and persisted facts, and carries source locations or an explicit
approximation label.

Phase 1 remains the foundation: safe ingestion, `ast` parsing, symbols, imports,
calls, inheritance, conservative resolution, the NetworkX code graph, SQLite
persistence, and the FastAPI surface. Phase 2 does not replace or weaken any
Phase 1 endpoint.

---

## 1. Status matrix (P2-01 … P2-12)

Status values: **Complete**, **Partial**, **Missing**, **Blocked**. A status is
only *Complete* for the scope and acceptance criteria stated in the row. Known
approximations are listed rather than hidden.

| ID | Work package | Status | Evidence | Known gap / approximation | Acceptance criteria |
|---|---|---|---|---|---|
| P2-01 | Function-level structural metrics | **Complete** | `repolens/analysis/function_metrics.py`; `function_metrics` table; `GET /api/repositories/{rid}/metrics/functions`; `tests/unit/test_function_metrics.py`, `tests/integration/test_api_metrics.py` | Cyclomatic complexity is an AST decision-point estimate, not a CFG cyclomatic number. Nested function/class/lambda bodies are excluded. | One record per function/method with exact statement/control counts, line range, nesting, `symbol_id`; persisted atomically; paginated and filterable by `symbol_id`. |
| P2-02 | Per-function control-flow graph | **Complete** | `repolens/analysis/cfg.py`; `GET /api/symbols/{sid}/cfg`; `tests/unit/test_cfg.py`, comprehension integration tests | Exception edges are conservative (all try-body nodes → all handlers). `return`/`break`/`continue` inside `try` do not route through `finally`. Exception types and propagation are not modelled. | Deterministic CFG with entry/exit, branches and merges, loops/back edges, break/continue, return/raise, try/try*/with, match/case, nesting, empty bodies, post-termination handling, source locations, caps and truncation. |
| P2-03 | Local data flow | **Complete** for the declared intraprocedural subset | `repolens/analysis/dataflow.py`; `GET /api/symbols/{sid}/dataflow`; `tests/unit/test_dataflow.py` | No interprocedural, alias, points-to, attribute, global, or whole-program analysis. Lambda/comprehension interiors are excluded. `undefined_uses` means “no locally reaching definition”, not a runtime error. | Local definitions/uses, parameters, reassignment kills, branch/loop joins, read-before-def, augmented assignment, with/except targets, global/nonlocal declarations, match guards, def-use links, exact deterministic IDs, explicit bounds. |
| P2-04 | Evidence-backed explanations | **Complete** | `repolens/analysis/explanations.py`; five explanation routes; `tests/unit/test_explanations.py`, comprehension integration tests | Templates are deterministic and finite. They explain structural facts, not programmer intent. Unknowns are reported when source, metrics, or caps prevent an answer. | Statement/function/file/folder/project levels; function explanations list bounded control-flow constructs; separate `facts`, `heuristics`, `unknowns`, `evidence`; every factual claim cites source/symbol/metric/relationship; no invented intent. |
| P2-05 | Lexical search | **Complete** | `repolens/analysis/search.py`, `repolens/services/search_service.py`; `GET /api/repositories/{rid}/search`; `tests/unit/test_search.py` | Lexical only; no embeddings or semantic similarity. Text search reads current source for files in the latest run, bounded and truncated explicitly. | Symbol/identifier/text/token modes; kind/path filters; snippets and enclosing symbol links; documented integer ranking; stable tie-breaks; pagination; explicit empty-query/no-result/truncation behavior. |
| P2-06 | Cross-file tracing | **Complete** | `repolens/analysis/tracing.py`, `repolens/services/trace_service.py`; `/trace`, `/trace/connect`, `/dependents`; `tests/unit/test_trace.py` | Only `resolved_static` relationships are traversed. Unresolved/external/ambiguous/dynamic references are reported, never followed. A path is a static relationship path, **not** a runtime execution trace. | Forward/reverse/both traversal; relationship filters; bounded depth/nodes; cycle detection and duplicate suppression; explicit unresolved reporting; cross-file evidence; connect found/not-found; reverse file dependents. |
| P2-07 | Architecture analysis and review indicators | **Complete** | `repolens/analysis/architecture.py`, `repolens/services/architecture_service.py`; `/architecture`, `/architecture/findings`; `tests/unit/test_architecture.py` | Imports only for the dependency graph; calls are not module dependencies. Cycles are resolved-static import cycles. Findings are review indicators; metrics alone never label code defective. | Dependency summary, deterministic cycles, degree centrality, fan-in/fan-out, instability, complexity hotspots, entry points, evidence-linked findings with explicit formulas and thresholds. |
| P2-08 | Unified API workflow and errors | **Complete** | `repolens/api/routes/*.py`, `repolens/api/deps.py`, `repolens/main.py`; integration tests | Synchronous analysis is retained from Phase 1. CFG/data-flow/explanations/search/trace/architecture compute on demand from persisted facts plus bounded current-source reads. | Register → analyze → metrics → CFG/data-flow → explanations → search → trace → architecture works through one API; consistent 400/404/409/422; pagination; unknown/empty/stale behavior documented; Phase 1 endpoints preserved. |
| P2-09 | Persistence and run association | **Complete** | `repolens/persistence/database.py` (`SCHEMA_VERSION = 2`), `repolens/services/analysis_service.py`; reanalysis integration test | Only function metrics are persisted. All other Phase 2 products are derived on demand. `function_metrics.symbol_id` is an association string, not a foreign key. | Additive schema migration; repository/run/file/symbol associations; indexes; atomic replacement; stale detail cleanup; latest-completed-run semantics; no cross-repository leakage or partial latest-run state. |
| P2-10 | E2E and regression tests | **Complete** | `tests/integration/test_api_comprehension.py`, `tests/integration/test_phase2_end_to_end.py`; unit suites for every engine | The suite never executes analyzed code; the E2E fixture writes a marker only if it were executed, and the test asserts the marker is absent. | Fixture repositories with multi-file imports/calls, nested functions, branches/loops, exceptions, cycles, empty and parse-error files; real API assertions; exact expected values; reanalysis cleanup; source-change/unavailable behavior. |
| P2-11 | Documentation and API reference | **Complete** | This document, `docs/api.md`, `docs/architecture.md`, `docs/limitations.md`, `README.md` | Docs describe the implemented subset and approximations; they do not claim interprocedural or runtime analysis. | One authoritative Phase 2 plan plus accurate API reference covering formulas, CFG semantics, data-flow limits, explanation rules, search/trace/architecture semantics, bounds, errors, and reproduction steps. |
| P2-12 | Quality gates and Phase 1 preservation | **Complete** | Final verification section below; all Phase 1 and Phase 2 tests | Two existing tests skip on Windows because creating symlinks requires a privilege (`WinError 1314`); they are not counted as passing. | `pytest`, `ruff check`, `ruff format --check`, `mypy repolens`, and `git diff --check` pass; no Phase 1 regression; Phase 2 changes are reviewed as a single commit. |

### Explicit non-goals

These are **not missing features**; they are outside the declared Phase 2 scope:

- interprocedural data flow, alias analysis, points-to analysis, or whole-program
  value tracking;
- runtime execution traces, profiler data, or dynamic dispatch resolution;
- embeddings, semantic/vector search, LLM explanations, or generated prose;
- automatic defect verdicts from metrics or graph shape alone;
- executing, importing, or mutating analyzed repository code;
- claiming Python exception/finalization semantics beyond the conservative CFG
  approximations documented in §3.2.

---

## 2. End-to-end workflow

```text
POST /api/repositories                      register a local directory
POST /api/repositories/{rid}/analysis       parse + extract + resolve + persist
GET  /api/repositories/{rid}/metrics/functions
GET  /api/symbols/{sid}/cfg
GET  /api/symbols/{sid}/dataflow
GET  /api/repositories/{rid}/explanations/{statement|function|file|folder|project}
GET  /api/repositories/{rid}/search?q=...
GET  /api/repositories/{rid}/trace?symbol_id=...
GET  /api/repositories/{rid}/trace/connect?source_symbol_id=...&target_symbol_id=...
GET  /api/repositories/{rid}/dependents?rel_path=...
GET  /api/repositories/{rid}/architecture
GET  /api/repositories/{rid}/architecture/findings
```

All paths are repository-relative POSIX strings. Analysis is synchronous, as in
Phase 1. If re-analysis fails, the previous completed run remains the latest
usable run; partial facts are never exposed as a completed run.

**Unknown vs empty vs stale**

- Unknown repository: 404 `repository_not_found` for explanation/search/trace/
  architecture; Phase 1-style list endpoints (files, symbols, relationships,
  metrics) return an empty page.
- Repository exists but has no completed run: 404 `analysis_not_available` for
  explanation/search/trace/architecture; list endpoints return an empty page.
- Source changed since analysis: on-demand CFG/data-flow/explanations use the
  current source, set `source_changed_since_analysis` / report an unknown, and do
  not pretend the current source matches persisted facts.
- Source deleted or unreadable: CFG/data-flow return 409 `source_unavailable`;
  file explanations still use stored facts and report the missing source.
- Cross-repository symbol id: rejected as 400/404 rather than leaking another
  repository’s facts.

---

## 3. Engine semantics

### 3.1 P2-01 — Function structural metrics

For each function/method symbol in the latest completed run:

- `start_line`, `end_line`, `line_count` = `end_lineno - lineno + 1`;
- `statement_count` counts `ast.stmt` nodes in the function's own scope,
  including compound-statement headers and the definition statement of a
  nested function or class, but excluding statements inside nested
  function/class/lambda bodies;
- `parameter_count` counts positional-only, positional, keyword-only, `*args`,
  and `**kwargs`;
- `if_count`, `for_count`, `while_count`, `try_count`, `with_count`
  count statements in the function’s own scope; `match_count` counts match
  cases (one per case), because each case is a distinct decision point;
- `return_count` counts `return` statements;
- `call_count` counts `Call` nodes in the function’s own scope;
- `max_nesting_depth` is the maximum control-flow nesting reached by
  `if`/`for`/`while`/`try`/`with`/`match`; `elif` is treated as a sibling
  decision, not an extra nesting level;
- `cyclomatic_complexity` is:

```text
1 + if_count + for_count + while_count + except_handlers
  + match_cases + extra_boolean_operands
```

`extra_boolean_operands` is `len(values) - 1` for each `and`/`or` expression.
`try*` handlers count like `try` handlers. Nested function/class/lambda bodies
are deliberately excluded from the enclosing function’s counts.

This is an **AST decision-point approximation**, not a CFG cyclomatic number and
not a quality score.

### 3.2 P2-02 — Control-flow graphs

Every function/method has exactly one `entry` and one `exit` node. Node kinds:
`entry`, `exit`, `statement`, `condition`, `loop`, `try`, `handler`, `with`,
`match`, `case`, `merge`, `return`, `raise`, `break`, `continue`, `nested_def`.
Edge types: `normal`, `true`, `false`, `loop_body`, `loop_back`, `loop_exit`,
`break`, `continue`, `return`, `raise`, `exception`, `finally`, `case`,
`no_match`.

Semantics:

- simple statements become `statement` nodes; `return`/`raise` are terminal and
  connect to `exit`;
- `if`/`elif`/`else` produce a `condition` with `true`/`false` edges and an
  explicit `merge` for the join; `elif` is a sibling condition;
- `for`/`while` produce a `loop` header with `loop_body`, `loop_back`, and
  `loop_exit`; `break` targets the loop exit and `continue` targets the header;
- `with` produces a `with` node and a join after its body;
- `match` produces a `match` node, one `case` node per case, guarded cases keep
  the guard before the body, and a `no_match` edge is added for non-exhaustive
  matches (an unguarded wildcard/name/`_` case is treated as exhaustive; a
  guarded case is not assumed to match);
- `try`/`try*` create a `try` node and one `handler` per handler. Every node
  created inside the try body gets an `exception` edge to every handler.
  `else` runs only after normal body completion; `finally` joins normal and
  handled paths;
- nested function/class/lambda bodies are not descended into; a nested
  definition is one `nested_def` node;
- an empty body (`pass`) is represented by a `statement` node so the graph is
  connected;
- statements after `return`/`raise`/`break`/`continue` are unreachable and are
  omitted with a note; unreachable synthetic merges are omitted.

**Approximations (reported in `notes`):**

- exception types, implicit propagation, and match specificity are not modelled;
- `return`/`break`/`continue` inside `try` do not route through `finally`;
- the graph is a bounded structural model, not a Python bytecode CFG.

Construction is deterministic (source order, sorted frontier/node ids). Default
bounds: 5,000 nodes, 20,000 edges, nesting depth 200. A capped graph returns
`truncated=true` and a note.

### 3.3 P2-03 — Local reaching definitions

Scope: **intraprocedural may-analysis** on one function CFG.

Definitions are local bindings:

- parameters;
- assignment/annotation/delete targets;
- augmented-assignment targets;
- `for` targets;
- `with ... as` targets;
- `except ... as` names;
- import aliases;
- nested `def`/`class` names;
- walrus (`:=`) targets;
- `match` pattern bindings.

Uses are `Name` nodes in load context evaluated in the function’s own scope.
Lambda bodies and comprehension interiors have their own scopes and are not
analysed; the first comprehension iterable and lambda defaults are collected
because they are evaluated in the enclosing scope.

Equations (forward *may* analysis):

```text
OUT[n] = GEN[n] ∪ (IN[n] − KILL[n])
IN[n]  = ⋃ OUT[p] for p ∈ pred(n)
```

`GEN[n]` is the set of definitions on node `n`; a definition kills prior
definitions of the same name. Uses see `IN[n]`, except uses marked “after
definitions” on the same node (for example a `match` guard, or a use after a
walrus target in the same expression) see `OUT[n]`. Augmented-assignment reads
are modelled before the same-node definition.

Outputs: ordered `definitions` (`d0`, `d1`, …), `uses` (`u0`, `u1`, …),
`reaching` per use, `def_use` links, `undefined_uses`, `non_local_names`,
`truncated`, `source_changed_since_analysis`, `notes`.

`undefined_uses` means **no locally reaching definition**. The name may be a
global, builtin, closure variable, or comprehension variable. It is not a claim
of a runtime error. `global`/`nonlocal` declarations suppress the undefined
marker but are still reported in `non_local_names`.

Bounds: 5,000 definitions, 10,000 uses, 50,000 fixpoint iterations. Hitting a
cap sets `truncated=true` and adds a note. A truncated input CFG also makes the
data-flow result `truncated=true`, because the analysis covers only the modelled
nodes.

Not claimed: interprocedural flow, alias/points-to, attribute fields, module
globals, dynamic dispatch, or whole-program values.

### 3.4 P2-04 — Explanations

Explanations are built from deterministic templates over persisted facts,
metrics, CFG, data flow, relationships, and architecture. They contain:

- `facts` — directly derived statements with source/symbol references;
- `heuristics` — review guidance, explicitly labelled (for example complexity
  thresholds or path-based grouping);
- `unknowns` — missing metrics, truncation, source changes, parse errors, and
  scope limitations;
- `evidence` — machine-readable references (`file`, `symbol`, `relationship`,
  `metric`, `cfg`, `line`) with repository-relative paths and line ranges;
- `sections` — short source/definition/dependency excerpts where useful.

Levels:

- **statement** — the source-backed CFG node covering one line, its incoming and
  outgoing edges, local definitions/reads, and control-flow role. Synthetic
  `entry`/`exit`/`merge` nodes are not preferred over real statements.
- **function** - signature, parameters, metrics, a bounded list of control-flow
  constructs (conditions, loops, cases, exception handlers, `with` statements),
  CFG/data-flow counts, outgoing and incoming calls, and review heuristics.
- **file** — parse status, symbols, top-level definitions, imports, resolved
  outgoing/incoming dependencies, metrics summary, entry point, and conservative
  resolution notes.
- **folder** — path-prefix grouping: file/symbol counts, internal and
  cross-boundary import edges, cycles entirely inside the folder, and
  highest-complexity functions.
- **project** — repository-wide file/symbol counts, resolved dependency edges,
  weakly connected components, cycles, central files, complexity hotspots,
  findings, entry points, and thresholds.

Every level distinguishes facts from heuristics and states when a result cannot
be determined. The templates never claim programmer intent.

### 3.5 P2-05 — Lexical search

Search is lexical and deterministic, with no embeddings. Modes:

- `all` (default) — symbol matching plus case-insensitive substring text search;
- `symbol` — symbol names/qualified names only;
- `identifier` — dotted Python identifier validation, then symbol matching;
- `text` — case-insensitive substring over current source lines;
- `token` — all whitespace-separated query tokens must appear as whole words on
  the line.

Symbol scoring (integer, higher first):

| Score | Reason |
|---|---|
| 100 | exact symbol name |
| 90 | symbol name prefix |
| 80 | symbol name substring |
| 75 | exact qualified name |
| 70 | qualified name suffix |
| 60 | qualified name substring |
| 50 + token count | all query tokens in symbol name/qualified name |

Text scoring:

| Score | Reason |
|---|---|
| 50 | exact trimmed line |
| 45 | whole-word substring |
| 40 | substring |

Ties break by repository-relative path, line, hit kind, qualified name, and
snippet. Text hits on a definition line are deduplicated against the symbol hit.
A text hit is linked to the **innermost enclosing symbol** (smallest span, then
deepest nesting). `symbol`/`identifier` modes never read source; `text`/`token`/
`all` read current source for parsed files in the latest run.

Limits: `limit` 1–500 (default 50), `offset ≥ 0`; `search_max_files` 2,000;
`search_max_hits` 1,000; at most 50 hits per file. Truncation is explicit.
An empty/whitespace query is 400 `invalid_request`; a missing `q` is 422.
No results is `items=[]`, `page.total=0`; `truncated` is false unless a file/hit
bound was hit.

### 3.6 P2-06 — Static tracing

Traversal uses persisted relationships, not only graph edges. It follows only
`resolved_static` relationships with a target symbol. Unresolved, external,
ambiguous, and dynamic references are reported at the node where they originate
and are never traversed.

- `direction=out` — what can this symbol reach?
- `direction=in` — what can reach this symbol?
- `direction=both` — explore both directions. Because an edge is visible from
  both indexes, the immediate reverse of the edge just traversed is not reported
  as a two-node cycle. Cycle reporting is traversal-order dependent; the
  architecture endpoint remains authoritative for import cycles.
- `rel_types` filters `imports`, `calls`, `inherits_from` (comma-separated).
- An `IN` step keeps the stored edge direction, so evidence always reflects the
  repository’s actual relationship.
- BFS order is deterministic; duplicate paths are suppressed by the visited set;
  edges that close an ancestor cycle are recorded in `cycles` while traversal
  continues.
- Unresolved references originating at visited nodes are reported even at the
  depth/node boundary.

`/trace/connect` returns `found`, the node-id path, edges, `length`, or an
explicit `reason` (for example no static relationship path or a depth cap).
`/dependents?rel_path=` is a file-level reverse import trace: resolved imports
are mapped to the **target symbol’s file**, then the reverse file graph is
traversed. Each file is represented by its module symbol. Files without a module
symbol (for example parse errors) return 404 `file_not_found`.

Limits: `max_depth` 1–8 (default 4, hard API cap 8); `max_nodes` 1–5,000
(default 500). Hitting a cap sets `truncated=true` and notes the bound.

**A trace is a static relationship path, not a runtime execution path.**

### 3.7 P2-07 — Architecture analysis

Graph: one node per analyzed file; one directed edge per distinct
`resolved_static` **import** between two different files. Multiple imports of the
same target collapse to one edge, keeping the earliest `(line, evidence)`.
Calls are not module dependencies for cycle detection.

- **Dependency cycles** — bounded `networkx.simple_cycles` over the import graph.
  Each cycle is canonically rotated to its lexicographically smallest file, then
  sorted by `(length, node sequence)`. Default max cycles 50, max cycle length 10.
  Cycle findings are objective graph facts (`heuristic=false`) and explicitly do
  not mean the code is wrong.
- **Central components** — degree centrality:

```text
centrality(file) = (in_degree + out_degree) / (N − 1)   for N > 1
                 = 0                                     for N ≤ 1
```

  Rounded to 6 decimals; sorted by `(-centrality, -degree, rel_path)`; top 10
  returned. One central-component finding is emitted for the top component with
  degree > 0 (`heuristic=true`).
- **Coupling** — fan-in/fan-out are distinct predecessor/successor files.
  Instability is:

```text
instability = fan_out / (fan_in + fan_out)   when fan_in + fan_out > 0
            = null                            otherwise
```

  Sorted by `(-(fan_in + fan_out), rel_path)`, capped at 50 displayed rows.
- **Complexity hotspots** — the P2-01 AST approximation, sorted by
  `(-complexity, -line_count, rel_path, start_line, qualified_name)`, top 10.
- **Entry points** — only explicit `if __name__ == "__main__"` guards and
  `__main__.py` files. Entry-point scan is bounded by 500 files.
- **Findings** — `dependency_cycle`, `central_component`, `high_fan_in`,
  `high_fan_out`, `complexity_hotspot`, `disconnected_components`. Each carries
  severity, subject, metrics, evidence, and `heuristic`. Defaults: complexity
  warning 10, fan-out warning 10, fan-in warning 10. Findings are sorted by
  severity (warning before info), kind, subject, and id; capped at 100 with
  `truncated=true`.

No finding labels code defective from a metric alone.

### 3.8 P2-08 — Unified API and errors

New domain errors and HTTP mappings:

| Code | HTTP | Meaning |
|---|---|---|
| `source_unavailable` | 409 | Current source for a persisted symbol/file cannot be read |
| `analysis_not_available` | 404 | Repository has no completed analysis run |
| `invalid_request` | 400 | Empty search, invalid identifier, bad rel type, cross-repo symbol, etc. |
| `repository_not_found` | 404 | Unknown repository |
| `file_not_found` | 404 | Path not in the latest completed run |
| `symbol_not_found` | 404 | Unknown symbol or symbol from another repository |

Error envelope: `{"error": {"code": "...", "detail": "..."}}`. Validation
errors use FastAPI’s 422 format. Pagination is `PageMeta(total, limit, offset)`.
File, symbol, comprehension and error payloads never return absolute host paths;
repository metadata returns the registered `root_path` for the operator.

### 3.9 P2-09 — Persistence and latest-run semantics

Schema version is **2**. Phase 2 adds:

- `function_metrics` table (one row per function/method) including `symbol_id`
  and `statement_count`;
- indexes on `(run_id)`, `(repository_id, run_id)`, and additive
  `(symbol_id)` after migration;
- additive migration for databases created before `symbol_id` or
  `statement_count` existed.

Only metrics are persisted. CFG, data flow, explanations, search, trace, and
architecture are computed on demand from persisted facts plus bounded current
source reads. This avoids caching stale derived state and keeps re-analysis
atomic. Details from previous runs are removed when a new completed run replaces
them; the latest **completed** run is the only source of persisted details. A
failed re-analysis does not replace the previous completed run.

### 3.10 P2-10 — Tests and fixtures

- Unit: `test_cfg.py`, `test_dataflow.py`, `test_explanations.py`,
  `test_search.py`, `test_trace.py`, `test_architecture.py`,
  `test_function_metrics.py`.
- Integration: `test_api_metrics.py`, `test_api_comprehension.py`,
  `test_phase2_end_to_end.py`.
- The E2E fixture includes multi-file imports/calls, nested functions, a loop,
  a branch, exception handling, a dependency cycle, an empty file, a parse-error
  file, and a marker-writing file that must never execute. The test asserts the
  marker is absent after both analysis and re-analysis.
- Assertions use exact expected values for node/edge sequences, data-flow IDs,
  scores, counts, centrality/coupling values, and cleanup behavior. Analyzed
  code is never executed.

### 3.11 P2-11 — Documentation

This plan is authoritative for Phases 2 semantics. `docs/api.md` is the endpoint
reference. `docs/architecture.md` describes where Phase 2 fits in the system.
`docs/limitations.md` states the honest scope. `README.md` is the entry point.

### 3.12 P2-12 — Quality gates

Required verification commands:

```bash
python -m pytest tests -o addopts="" -p no:cacheprovider
python -m ruff check .
python -m ruff format --check .
python -m mypy repolens
git diff --check
```

Phase 2 is committed as a single reviewable commit. No analyzed repository
code is imported or executed by tests or analysis.

---

## 4. Bounds and thresholds

| Setting | Default | Hard/API limit | Effect |
|---|---:|---:|---|
| `cfg_max_nodes` | 5,000 | — | CFG node cap |
| `cfg_max_edges` | 20,000 | — | CFG edge cap |
| `cfg_max_depth` | 200 | — | CFG nesting cap |
| `dataflow_max_definitions` | 5,000 | — | Definition collection cap |
| `dataflow_max_uses` | 10,000 | — | Use collection cap |
| `dataflow_max_iterations` | 50,000 | — | Fixpoint iteration cap |
| `trace_max_depth` | 4 | 8 | Default/max trace depth |
| `trace_max_nodes` | 500 | 5,000 | Default/max trace nodes |
| `search_max_files` | 2,000 | — | Files read per text search |
| `search_max_hits` | 1,000 | — | Combined hit cap |
| `architecture_max_cycles` | 50 | — | Cycle enumeration cap |
| `architecture_max_cycle_length` | 10 | — | Simple-cycle length bound |
| `architecture_top_components` | 10 | — | Central/hotspot display cap |
| `architecture_max_findings` | 100 | — | Findings cap |
| `architecture_entry_scan_limit` | 500 | — | Entry-point scan cap |
| `complexity_warning_threshold` | 10 | — | Complexity finding threshold |
| `fan_out_warning_threshold` | 10 | — | Fan-out finding threshold |
| `fan_in_warning_threshold` | 10 | — | Fan-in finding threshold |

API page sizes: 1–500 (default 50), except run history (1–200). All truncation is
reported in `truncated` and/or `notes`.

---

## 5. Reproduction

```bash
pip install -e ".[dev]"
python -m pytest tests -o addopts="" -p no:cacheprovider
python -m ruff check .
python -m ruff format --check .
python -m mypy repolens
git diff --check
```

Manual API smoke flow:

```bash
python -m repolens.main
# register and analyze a local repository, then:
# GET /api/repositories/<rid>/metrics/functions
# GET /api/symbols/<sid>/cfg
# GET /api/symbols/<sid>/dataflow
# GET /api/repositories/<rid>/explanations/project
# GET /api/repositories/<rid>/search?q=main
# GET /api/repositories/<rid>/trace?symbol_id=<sid>&direction=out
# GET /api/repositories/<rid>/architecture
```

---

## 6. Verification record

Verified on 2026-10-21:

- `python -m pytest tests -o addopts="" -p no:cacheprovider` —
  **252 passed, 2 skipped**;
- the two skips are Windows symlink-privilege tests (`WinError 1314`) and are
  not counted as passing;
- `python -m ruff check .` — clean;
- `python -m ruff format --check .` — clean;
- `python -m mypy repolens` — success;
- `git diff --check` — clean.

Phase 2 is committed as a single reviewable commit without a push.
