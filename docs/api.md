# RepoLens HTTP API Reference

Base URL: `http://<host>:<port>` (default `http://127.0.0.1:8000`).
All endpoints are under `/api`, except `/health`. Interactive docs are served
at `/docs` (Swagger UI) and the schema at `/openapi.json`.

## Conventions

- **Pagination**: list endpoints accept `limit` (default 50, max 500) and
  `offset` (≥ 0) and return `{"items": [...], "page": {"total", "limit", "offset"}}`.
- **Error envelope**: every domain error returns
  `{"error": {"code": "<machine_code>", "detail": "<message>"}}` with an
  appropriate HTTP status. Validation errors use FastAPI's `422` format.
- **Paths**: file/symbol/graph/comprehension responses use repository-relative
  **POSIX** strings. Repository metadata (`POST/GET /api/repositories…`) also
  returns the registered `root_path` for the operator; file and analysis
  endpoints never return absolute host paths.

### Error codes

| Code | HTTP | Raised when |
|---|---|---|
| `repository_not_found` | 404 | Unknown repository id |
| `repository_exists` | 409 | Root path already registered |
| `invalid_repository_path` | 400 | Registration path is not an existing directory |
| `analysis_run_not_found` | 404 | Unknown analysis run id |
| `analysis_failed` | 500 | A run failed unexpectedly (also recorded on the run) |
| `file_not_found` | 404 | Relative path not part of the latest completed run |
| `symbol_not_found` | 404 | Unknown symbol id, or a symbol from another repository |
| `analysis_not_available` | 404 | Repository has no completed analysis run |
| `source_unavailable` | 409 | Current source for a persisted symbol/file cannot be read |
| `invalid_request` | 400 | Empty/invalid search, bad relationship type, cross-repository symbol, invalid line |
| `graph_node_not_found` | 404 | Node id unknown or not in the latest run |
| `path_traversal` | 400 | Malformed or escaping relative path |
| `graph_integrity` | 500 | Graph construction detected inconsistent facts |
| `internal_error` | 500 | Unexpected error (no stack trace or path is leaked) |

---

## Health

### `GET /health`

```json
{ "status": "ok", "version": "0.1.0" }
```

---

## Repositories

### `POST /api/repositories`

Register a local repository by absolute path. The path is resolved; a missing
path or a file (not directory) yields `400`.

Request:

```json
{ "name": "my-project", "path": "/abs/path/to/my-project" }
```

Response `201`:

```json
{
  "id": "repo_1a2b…",
  "name": "my-project",
  "root_path": "/abs/path/to/my-project",
  "registered_at": "2026-10-18T12:00:00",
  "latest_run": null
}
```

The `id` is deterministic (SHA-256 of the normalized absolute path). `400`
invalid path, `409` duplicate.

### `GET /api/repositories`

List registered repositories (oldest first). Response `200`: `list[Repository]`.

### `GET /api/repositories/{repository_id}`

Repository metadata plus its **latest analysis run** (or `null`). `404` when
unknown.

---

## Analysis

### `POST /api/repositories/{repository_id}/analysis`

Run the pipeline **synchronously** and persist the result atomically. Returns
the completed run. `404` unknown repository; on an unexpected pipeline failure
the run is recorded as `failed` and `500` (`analysis_failed`) is returned
without disturbing the previous completed run.

Response `201`:

```json
{
  "id": "run_…",
  "repository_id": "repo_…",
  "status": "completed",
  "started_at": "2026-10-18T12:00:00",
  "finished_at": "2026-10-18T12:00:01",
  "summary": {
    "files_discovered": 3,
    "files_excluded": 0,
    "excluded_directories": 0,
    "files_read": 2,
    "files_parsed": 1,
    "files_read_errors": 1,
    "files_parse_errors": 1,
    "symbols_extracted": 7,
    "relationships_extracted": 8,
    "relationships_resolved": 2,
    "relationships_unresolved": 1,
    "relationships_external": 5,
    "relationships_ambiguous": 0,
    "relationships_dynamic": 0,
    "warnings": 0,
    "errors": 2,
    "duration_seconds": 0.012,
    "truncated": false
  },
  "error": null
}
```

### `GET /api/repositories/{repository_id}/runs`

Run history, newest first. Query: `limit` (1–200, default 50).
Response `200`: `list[AnalysisRun]`.

### `GET /api/analysis/runs/{run_id}`

Single run by id. `404` when unknown.

### `GET /api/analysis/runs/{run_id}/errors`

Paginated errors and warnings recorded for a run (read errors, parse errors,
discovery exclusions, limit warnings). Response `200`: `PaginatedIssues`.

```json
{
  "items": [
    {
      "id": 1,
      "run_id": "run_…",
      "repository_id": "repo_…",
      "severity": "error",
      "category": "parse_error",
      "message": "invalid syntax",
      "rel_path": "broken.py",
      "line": 1,
      "offset": 11
    }
  ],
  "page": { "total": 2, "limit": 50, "offset": 0 }
}
```

---

## Files

### `GET /api/repositories/{repository_id}/files`

Paginated files of the latest completed run. `limit`/`offset` as above.
Response `200`: `PaginatedFiles`.

### `GET /api/repositories/{repository_id}/files/{rel_path:path}`

File metadata, optionally with a source slice.

| Query | Type | Meaning |
|---|---|---|
| `include_source` | bool (default false) | Re-read the file and return text |
| `start_line` | int ≥ 1 | 1-based inclusive start of the slice |
| `end_line` | int ≥ 1 | 1-based inclusive end of the slice |

Source text is **never stored in the database**; it is re-read from disk with
strict root-boundary validation on every request. `source_available` reports
whether the file could be read, and `content_changed_since_analysis` compares
the on-disk hash with the analyzed hash.

Response `200` (with `include_source=true`):

```json
{
  "id": "file_…",
  "repository_id": "repo_…",
  "run_id": "run_…",
  "rel_path": "simple.py",
  "module_name": "simple",
  "file_type": "python",
  "size_bytes": 381,
  "content_hash": "…",
  "parse_status": "parsed",
  "error_message": null,
  "error_line": null,
  "error_offset": null,
  "content": "…",
  "start_line": 1,
  "end_line": 24,
  "source_available": true,
  "content_changed_since_analysis": false
}
```

`400` traversal, `404` unknown path.

---

## Relationships

### `GET /api/repositories/{repository_id}/relationships`

Paginated relationships of the latest completed run.

| Query | Meaning |
|---|---|
| `rel_type` | `imports` \| `calls` \| `inherits_from` |
| `status` | `resolved_static` \| `unresolved` \| `ambiguous` \| `external` \| `dynamic_or_unknown` |
| `symbol_id` | match source **or** target symbol |

Every relationship carries provenance (`evidence`, `source_rel_path`,
`source_line`, `source_col`) and an explicit `resolution_status`. Only
`resolved_static` relationships have a non-null `target_symbol_id`; the others
are preserved as records and are **not** materialised as graph edges.

---

## Symbols

### `GET /api/repositories/{repository_id}/symbols`

| Query | Meaning |
|---|---|
| `file_id` | restrict to one file node id |
| `kind` | `module` \| `class` \| `function` \| `method` |
| `name` | exact simple-name match |

Response `200`: `PaginatedSymbols`.

### `GET /api/symbols/{symbol_id}`

Symbol detail including `qualified_name`, `signature`, `parameters`,
`return_annotation`, `bases`, `decorators`, `docstring`, parent, and line range.

### `GET /api/symbols/{symbol_id}/parent`

Enclosing symbol, or JSON `null` for module-level symbols. `404` unknown id.

### `GET /api/symbols/{symbol_id}/children`

Symbols nested directly inside this symbol. `404` unknown id.

---

## Graph

The directed graph is rebuilt from persisted facts per request. Nodes are
`repository` (id = `repo_` + repository id), `file` (`file_*`) and symbol
nodes (`sym_*`); edges are `contains` (structural) plus `imports`, `calls`,
`inherits_from` (from `resolved_static` relationships only). List endpoints
use `limit` (default 50, max 500) / `offset`.

### `GET /api/repositories/{repository_id}/graph/stats`

```json
{
  "repository_id": "repo_…",
  "nodes": 10,
  "edges": 9,
  "by_node_type": { "repository": 1, "file": 2, "class": 3, "method": 4 },
  "by_edge_type": { "contains": 9, "calls": 1 }
}
```

### `GET /api/repositories/{repository_id}/graph/nodes`

Paginated nodes; optional `type` filter. Response `200`: `PaginatedNodes`.

### `GET /api/repositories/{repository_id}/graph/edges`

Paginated edges; optional `type` filter. Response `200`: `PaginatedEdges`.
Edge `sites` merge all provenance (line, evidence, source symbol) for the same
`(source, target, type)`.

### `GET /api/graph/nodes/{node_id}/neighbors`

| Query | Meaning |
|---|---|
| `edge_type` | filter by edge type |
| `direction` | `in` \| `out` \| `both` (default `both`) |
| `limit` | max neighbors (default 50, max 500) |

Response `200`: `NeighborResult` (`node`, `neighbors`, `edges`). The owning
repository is resolved from the node id, so no repository id is required.
`404` unknown node.

### `GET /api/graph/path?source=…&target=…&max_depth=…`

Breadth-first shortest directed path, bounded by `max_depth` (≤ 10) and a
visited-node cap. When no path exists the response is explicit and honest:

```json
{ "found": false, "source": "…", "target": "…", "nodes": [], "length": 0,
  "reason": "no path within max_depth=6" }
```

A path is **never fabricated**.

### `GET /api/graph/nodes/{node_id}`

Single node by id. `404` unknown.

---

## Metrics (Phase 2)

### `GET /api/repositories/{repository_id}/metrics/functions`

Paginated function-level structural metrics of the latest completed run.
Query: `symbol_id` (optional), `limit` (1–500, default 50), `offset`.
Response `200`: `PaginatedFunctionMetrics` (`id`, `repository_id`, `run_id`,
`symbol_id`, `file_id`, `rel_path`, `qualified_name`, `start_line`, `end_line`,
`parameter_count`, `line_count`, `statement_count`,
`if_count`, `for_count`, `while_count`, `try_count`, `with_count`,
`match_count`, `return_count`, `call_count`, `max_nesting_depth`,
`cyclomatic_complexity`). Deterministic order: `rel_path`, `start_line`,
`qualified_name`.

`cyclomatic_complexity` is an **AST decision-point approximation**:

```text
1 + if + for + while + except-handlers + match-cases + extra boolean operands
```

It is not a CFG cyclomatic number. Nested function/class/lambda bodies are
excluded from the enclosing function’s counts. Unknown repositories and
repositories without a completed run return an empty page, consistent with the
other Phase 1 list endpoints.

---

## Control-flow graphs (Phase 2)

### `GET /api/symbols/{symbol_id}/cfg`

Builds the CFG for one persisted function/method symbol from the **current**
source of its file. The symbol must belong to the latest completed run.

Response `200`: `FunctionCFG`:

| Field | Meaning |
|---|---|
| `symbol_id`, `qualified_name`, `file_id`, `rel_path` | symbol association |
| `start_line`, `end_line` | function line range |
| `entry`, `exit` | entry/exit node ids |
| `nodes` | `{id, kind, label, line, end_line, column, source}` |
| `edges` | `{source, target, type, line, evidence}` |
| `truncated` | a node/edge/depth cap was hit |
| `source_changed_since_analysis` | current file hash differs from the analyzed hash |
| `notes` | explicit approximation/truncation notes |

Node kinds: `entry`, `exit`, `statement`, `condition`, `loop`, `try`, `handler`,
`with`, `match`, `case`, `merge`, `return`, `raise`, `break`, `continue`,
`nested_def`. Edge types: `normal`, `true`, `false`, `loop_body`, `loop_back`,
`loop_exit`, `break`, `continue`, `return`, `raise`, `exception`, `finally`,
`case`, `no_match`.

Semantics and approximations are specified in
[`docs/phase-2-plan.md`](phase-2-plan.md) §3.2. In particular, exception edges
are conservative and `return`/`break`/`continue` inside `try` do not route
through `finally`.

Errors: `404 symbol_not_found`; `400 invalid_request` for a non-function symbol;
`409 source_unavailable` when the file cannot be read.

---

## Local data flow (Phase 2)

### `GET /api/symbols/{symbol_id}/dataflow`

Bounded **intraprocedural may reaching-definitions** analysis on the CFG of one
function/method.

Response `200`: `DataFlowResult`:

| Field | Meaning |
|---|---|
| `definitions` | `{id, name, kind, node_id, line, column}`, ordered `d0…` |
| `uses` | `{id, name, node_id, line, column}`, ordered `u0…` |
| `reaching` | `{use_id, definitions: [definition ids]}` |
| `def_use` | `{definition_id, uses: [use ids]}` |
| `undefined_uses` | uses with **no locally reaching definition** |
| `non_local_names` | names declared `global`/`nonlocal` |
| `truncated`, `notes` | caps and scope limitations |
| `source_changed_since_analysis` | current source hash differs from the analyzed hash |

Definition kinds: `parameter`, `assignment`, `augmented_assignment`,
`loop_target`, `with_target`, `except_name`, `import`, `nested_def`, `walrus`,
`delete`, `pattern`.

`undefined_uses` is not a runtime-error claim: the name may be a global,
builtin, closure variable, or comprehension variable. No interprocedural,
alias, points-to, attribute, or whole-program analysis is claimed. Lambda and
comprehension interiors are separate scopes and are not analysed.

Errors: `404 symbol_not_found`; `400 invalid_request` for a non-function symbol;
`409 source_unavailable` when the file cannot be read.

---

## Explanations (Phase 2)

All explanation levels return `Explanation`:

```json
{
  "level": "function",
  "subject": "pkg.mod.f",
  "title": "Function pkg.mod.f",
  "facts": ["..."],
  "heuristics": ["..."],
  "unknowns": ["..."],
  "evidence": [
    {"kind": "symbol", "rel_path": "pkg/mod.py", "line": 3,
     "end_line": 9, "symbol_id": "sym_…", "detail": "function symbol"}
  ],
  "sections": [{"title": "Signature", "lines": ["..."], "evidence": []}]
}
```

`facts` are derived directly from source/persisted facts; `heuristics` are
review guidance; `unknowns` state missing metrics, truncation, source changes,
parse errors, and scope limits; `evidence` is machine-readable
(`file`/`symbol`/`relationship`/`metric`/`cfg`/`line`). Templates are
deterministic and never claim programmer intent. See
[`docs/phase-2-plan.md`](phase-2-plan.md) §3.4.

### `GET /api/repositories/{repository_id}/explanations/function/{symbol_id}`

Function signature, parameters, metrics, a bounded control-flow construct
summary (conditions, loops, cases, handlers, `with` statements), CFG/data-flow
counts, incoming and outgoing calls, and heuristics. The symbol must belong to
the URL repository.

### `GET /api/repositories/{repository_id}/explanations/statement/{symbol_id}?line=N`

Explains the source-backed CFG node covering one-based line `N`, including
incoming/outgoing edges and local definitions/reads. Synthetic
`entry`/`exit`/`merge` nodes are not preferred over real statements. A line with
no source-backed node returns an explanation with an `unknowns` entry.

### `GET /api/repositories/{repository_id}/explanations/file/{rel_path:path}`

Parse status, symbols, top-level definitions, imports, resolved outgoing and
incoming dependencies, metrics summary, entry point, and conservative-resolution
notes. Parse-error files still return stored metadata and an explicit unknown.

### `GET /api/repositories/{repository_id}/explanations/folder?prefix=...`

Path-prefix grouping: file/symbol counts, internal and cross-boundary import
edges, cycles entirely inside the folder, and highest-complexity functions.
`prefix` defaults to the repository root.

### `GET /api/repositories/{repository_id}/explanations/project`

Repository-wide counts, resolved dependency edges, weakly connected components,
cycles, central files, complexity hotspots, findings, entry points, thresholds,
and architecture notes.

Errors: `404 repository_not_found`, `404 analysis_not_available`,
`404 file_not_found`, `404 symbol_not_found`, `400 invalid_request` for a
cross-repository symbol or a statement line outside the function.

---

## Search (Phase 2)

### `GET /api/repositories/{repository_id}/search`

Deterministic lexical search over stored symbols and current source of parsed
files in the latest completed run. No embeddings or semantic similarity.

| Query | Type | Meaning |
|---|---|---|
| `q` | string, required, 1–500 | search text |
| `mode` | `all` \| `symbol` \| `identifier` \| `text` \| `token` | default `all` |
| `kind` | `module` \| `class` \| `function` \| `method` | restrict hits |
| `path_prefix` | string | repository-relative path prefix |
| `limit` | int 1–500 | default 50 |
| `offset` | int ≥ 0 | default 0 |

Modes:

- `all` — symbol matching plus case-insensitive substring text search;
- `symbol` — symbols only;
- `identifier` — validate a dotted Python identifier, then symbols only;
- `text` — case-insensitive substring over source lines;
- `token` — all whitespace-separated tokens must appear as whole words on the
  line.

Response `200`: `PaginatedSearchHits` with `items`, `page`, and `truncated`.
Each `SearchHit` has `kind` (`symbol`/`text`), `score`, `rel_path`, `line`,
`end_line`, `snippet`, `matched_text`, `symbol_id`, `qualified_name`,
`symbol_kind`, and `match_reason`. Text hits link to the innermost enclosing
symbol.

Symbol score order: exact name 100, name prefix 90, name substring 80, exact
qualified name 75, qualified suffix 70, qualified substring 60, all tokens
50 + token count. Text score: exact line 50, whole word 45, substring 40. Ties
break by path, line, hit kind, qualified name, snippet. Definition-line text
hits are deduplicated against symbol hits.

Empty/whitespace query → `400 invalid_request`; invalid identifier query →
`400 invalid_request`; missing `q` → 422. No results → `items=[]`,
`page.total=0`; `truncated` is `false` unless a search file/hit bound was hit.
Empty source/unreadable files are skipped; truncation is explicit.

Errors: `404 repository_not_found`, `404 analysis_not_available`,
`400 invalid_request`.

---

## Tracing (Phase 2)

### `GET /api/repositories/{repository_id}/trace`

Bounded static relationship traversal from one symbol.

| Query | Type | Meaning |
|---|---|---|
| `symbol_id` | string, required | root symbol in this repository |
| `direction` | `out` \| `in` \| `both` | default `out` |
| `rel_types` | comma-separated | `imports`, `calls`, `inherits_from` |
| `max_depth` | int 1–8 | default 4 |
| `max_nodes` | int 1–5000 | default 500 |

Only `resolved_static` relationships with a target are followed. Unresolved,
external, ambiguous, and dynamic references are reported at the node where they
originate and are never traversed. An `in` step keeps the stored edge direction
in evidence. Duplicate paths are suppressed; ancestor cycles are reported in
`cycles`. Unresolved references at the depth/node boundary are still reported.

Response `200`: `TraceResult` with `repository_id`, `root_symbol_id`,
`direction`, `max_depth`, `max_nodes`, `nodes` (`symbol_id`, `qualified_name`,
`kind`, `rel_path`, `start_line`, `depth`, `via`,
`unresolved_references`), `edges`, `unresolved`, `cycles`, `truncated`, `notes`.

**A trace is a static relationship path, not a runtime execution trace.**

### `GET /api/repositories/{repository_id}/trace/connect`

Query: `source_symbol_id`, `target_symbol_id` (required), `direction`,
`max_depth` (1–8), `max_nodes` (1–5000). Returns `TraceConnection`:
`found`, `source_symbol_id`, `target_symbol_id`, `nodes` (symbol ids), `edges`,
`length`, `reason` (when not found), `truncated`. A missing path is explicit and
never fabricated.

### `GET /api/repositories/{repository_id}/dependents`

File-level reverse import trace: which files depend on `rel_path`?
Query: `rel_path` (required), `max_depth` (1–8), `max_nodes` (1–5000).
Resolved imports are mapped to the target symbol’s file and the reverse file
graph is traversed; each file is represented by its module symbol. Returns the
same `TraceResult` shape with `direction=in`.

Errors: `404 repository_not_found`, `404 analysis_not_available`,
`404 symbol_not_found` (including cross-repository symbols),
`404 file_not_found` (including files without a module symbol),
`400 invalid_request` for an unknown relationship type.

---

## Architecture (Phase 2)

### `GET /api/repositories/{repository_id}/architecture`

Dependency summary and review indicators derived only from persisted facts.

Response `200`: `ArchitectureSummary`:

| Field | Meaning |
|---|---|
| `files`, `dependency_edges` | analyzed files; distinct resolved-static import edges |
| `cycles` | `{files, edges}`; canonical, bounded simple import cycles |
| `central_components` | `{rel_path, in_degree, out_degree, degree, centrality}` |
| `coupling` | `{rel_path, fan_in, fan_out, instability}` |
| `complexity_hotspots` | `{symbol_id, qualified_name, rel_path, start_line, end_line, cyclomatic_complexity, line_count, max_nesting_depth}` |
| `findings` | evidence-linked review indicators |
| `disconnected_components` | weakly connected components in the import graph |
| `entry_points` | `{rel_path, line, evidence}` |
| `thresholds` | explicit formulas and thresholds |
| `notes`, `truncated` | scope and caps |

Formulas:

```text
centrality   = (in_degree + out_degree) / (N − 1)   for N > 1, else 0
instability  = fan_out / (fan_in + fan_out)          when denominator > 0, else null
```

The dependency graph uses **imports only**; calls are not module dependencies.
Cycles are resolved-static import cycles. Centrality/coupling are structural
indicators, not quality scores. Complexity hotspots use the P2-01 AST
approximation. Findings never label code defective from a metric alone.

Finding kinds: `dependency_cycle` (objective graph fact, `heuristic=false`),
`central_component`, `high_fan_in`, `high_fan_out`, `complexity_hotspot`,
`disconnected_components`. Default thresholds: complexity 10, fan-out 10,
fan-in 10. Findings are capped (100) and sorted warning-before-info.

### `GET /api/repositories/{repository_id}/architecture/findings`

Query: `kind` (string), `severity` (`info`/`warning`), `limit` (1–500, default
50), `offset` (≥ 0). Response `200`: `PaginatedFindings`.

Errors: `404 repository_not_found`, `404 analysis_not_available`.
