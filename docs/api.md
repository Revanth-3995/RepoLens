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
- **Paths**: file paths are repository-relative **POSIX** strings. No endpoint
  ever returns absolute host paths.

### Error codes

| Code | HTTP | Raised when |
|---|---|---|
| `repository_not_found` | 404 | Unknown repository id |
| `repository_exists` | 409 | Root path already registered |
| `invalid_repository_path` | 400 | Registration path is not an existing directory |
| `analysis_run_not_found` | 404 | Unknown analysis run id |
| `analysis_failed` | 500 | A run failed unexpectedly (also recorded on the run) |
| `file_not_found` | 404 | Relative path not part of the latest completed run |
| `symbol_not_found` | 404 | Unknown symbol id |
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
