# RepoLens — Architecture (Phase 1 and Phase 2)

Status: approved Phase 1 design, with Phase 2 comprehension sections 15.1–15.5.
Scope: repository ingestion, Python static analysis, graph construction, persistence, HTTP API, and deterministic comprehension.

## 0. Non-negotiable constraints

- No LLMs, no generative-AI APIs, no ML training, no GPU requirement.
- The analyzed repository's code is **never executed, imported, installed, or modified**.
- CPU-first modular monolith; SQLite + in-memory NetworkX graph; no external services.
- Source locations and evidence are preserved end to end.
- Python source only (`.py` files) in Phase 1.

## 1. System overview

```
┌─────────────┐   ┌──────────────────────────────────────────────────────────────┐
│  FastAPI    │──▶│ Services                                                     │
│  routes     │   │  RepositoryService      AnalysisService                      │
└─────────────┘   └───────┬──────────────────────┬───────────────────────────────┘
                          │                      │
                          │            ┌─────────▼───────────┐
                          │            │ AnalysisPipeline    │  (pure, in-memory)
                          │            │  discovery → read   │
                          │            │  → parse → symbols  │
                          │            │  → imports/calls    │
                          │            │  → resolve → graph  │
                          │            └─────────┬───────────┘
                          │                      │ AnalysisOutcome (facts + summary)
              ┌───────────▼──────────┐  ┌────────▼─────────┐
              │ persistence/         │  │ graph/           │
              │  Database (sqlite3)  │  │  builder,queries │
              │  repositories (DAL)  │  │  (NetworkX)      │
              └──────────────────────┘  └──────────────────┘
```

End-to-end flow:

1. `POST /api/repositories` — validate an absolute path, derive a stable repo id, persist.
2. `POST /api/repositories/{id}/analysis` — synchronously run the pipeline:
   discovery → read → parse → symbol extraction → import/call extraction →
   static resolution → graph construction + integrity validation →
   **single-transaction persistence** → run marked `completed`.
3. Query endpoints read persisted facts from SQLite; graph endpoints additionally rebuild
   the in-memory NetworkX graph from those facts.

The pipeline is a pure in-memory component (`analysis/pipeline.py`) that produces an
`AnalysisOutcome`. Persistence is owned by `AnalysisService`, which guarantees that a
failed run never corrupts the previous completed run's data.

## 2. Module boundaries and responsibilities

| Package | Responsibility | May depend on |
|---|---|---|
| `repolens.config` | Settings from environment; logging setup | stdlib only |
| `repolens.core` | Domain errors, stable identifiers, shared domain models/enums | stdlib, pydantic |
| `repolens.ingestion` | Safe discovery, exclusion policy, source reading | core, config |
| `repolens.analysis` | Parsing, symbol/import/call extraction, resolution, pipeline | core, ingestion, graph (builder for validation) |
| `repolens.graph` | NetworkX graph construction from facts; graph queries | core, networkx |
| `repolens.persistence` | SQLite schema, connections, data-access logic | core, stdlib sqlite3 |
| `repolens.services` | Orchestration: registration, analysis lifecycle, queries | all of the above |
| `repolens.api` | FastAPI routers, request/response models, error mapping | services |
| `repolens.main` | App factory, dependency wiring, startup | api, services, persistence |

No module below `services` knows about HTTP; no module above `persistence` touches SQL.

## 3. Core domain models (`repolens.core.models`)

All serialized forms use pydantic models; internal dataclasses are converted at boundaries.

- `Repository(id, name, root_path, registered_at, latest_run: RunSummary | None)`
- `AnalysisRun(id, repository_id, status, started_at, finished_at, summary, error)`
  - `status ∈ {running, completed, failed}`
- `AnalysisSummary` — all counts computed from actual results (see §10).
- `FileRecord(id, repository_id, rel_path, module_name, file_type, size_bytes,
  content_hash, parse_status, error_message, error_line, error_offset)`
  - `parse_status ∈ {parsed, read_error, parse_error}`
- `SymbolRecord(id, file_id, rel_path, name, qualified_name, kind, parent_id, docstring,
  signature, parameters, return_annotation, bases, decorators, start_line, end_line, ordinal)`
  - `kind ∈ {module, class, function, method}` (nested functions are `function` symbols
    whose parent is another function; nested classes are `class` symbols whose parent is
    a function/method — nesting is modeled by `parent_id`, not by extra kinds)
- `RelationshipRecord(id, source_file_id, source_symbol_id, source_rel_path, source_line,
  source_col, rel_type, target_symbol_id, target_ref, call_expr, resolution_status, evidence)`
  - `rel_type ∈ {imports, calls, inherits_from}`
  - `resolution_status ∈ {resolved_static, unresolved, ambiguous, external, dynamic_or_unknown}`
- `AnalysisIssue(id, severity, category, message, rel_path, line, offset)`
  - `severity ∈ {error, warning}`; categories: `read_error`, `parse_error`, `discovery`,
    `limit_exceeded`, `graph_integrity`
- `GraphNode(id, type, label, attrs)`, `GraphEdge(source, target, type, provenance)`,
  `PathResult(found, nodes, reason)`

Facts vs. inference vs. runtime (enforced by the model above):

1. **Extracted facts** — symbols, source locations, call/import expressions, docstrings,
   signatures: directly from the AST.
2. **Deterministically resolved relationships** — `resolved_static` edges: evidence string
   records the exact rule that fired.
3. **Heuristic/uncertain** — `ambiguous` / `dynamic_or_unknown` statuses: never presented
   as resolved.
4. **Runtime observations** — none collected in Phase 1 (documented, not simulated).

## 4. Identifier strategy (`repolens.core.identifiers`)

All identifiers are deterministic, opaque, prefixed, and derived with SHA-256:

```
stable_id(prefix, *parts) = prefix + "_" + sha256("\x1f".join(parts))[:24]
repo_id   = stable_id("repo", normalized_absolute_root_path)
file_id   = stable_id("file", repo_id, rel_path)
symbol_id = stable_id("sym", repo_id, rel_path, kind, qualified_name, ordinal)
rel_id    = stable_id("rel", repo_id, source_file_id, source_symbol_id|"",
                      rel_type, target_symbol_id|target_ref, line, col)
run_id    = "run_" + uuid4().hex   (per-run, not content-derived)
```

- `qualified_name` is the dotted scope chain: module name (derived from rel path,
  `pkg/mod.py → pkg.mod`, `pkg/__init__.py → pkg`) plus enclosing class/function names,
  e.g. `pkg.models.Person.greet`, `shapes.outer.inner`.
- `ordinal` disambiguates same `(qualified_name, kind)` redefinitions within one file
  (0-based, in source order). Two same-named functions in different modules never collide
  because `repo_id` and `rel_path` are part of the hash input.

**Stability guarantee (honest scope)**: symbol IDs are stable across edits that do not
change the symbol's file, kind, name, or enclosing scope chain, and across edits elsewhere
in the file (line numbers are *not* part of identity). IDs **change** when a symbol is
renamed, moved to a different scope/file, changes kind, or when an earlier duplicate
redefinition is added/removed (ordinal shift). This is documented, not over-promised.

## 5. Persistence model (SQLite)

Raw stdlib `sqlite3` (decision AD-5). One database file, configured via
`REPOLENS_DB_PATH` (default `repolens.db`). Every connection: `PRAGMA foreign_keys=ON`,
`journal_mode=WAL`, `row_factory=sqlite3.Row`. Schema created idempotently at startup;
`schema_meta` table records `schema_version` (currently `2`) and drives additive migrations.

Tables (columns abbreviated; exact DDL in `repolens/persistence/database.py`):

- `repositories(id PK, name, root_path UNIQUE, registered_at)`
- `analysis_runs(id PK, repository_id FK→repositories ON DELETE CASCADE, status CHECK,
  started_at, finished_at, summary_json, error)` + index `(repository_id, started_at DESC)`
- `files(id PK, repository_id FK, run_id FK→analysis_runs CASCADE, rel_path, module_name,
  file_type, size_bytes, content_hash, parse_status CHECK, error_message, error_line,
  error_offset, UNIQUE(run_id, rel_path))` + indexes
- `symbols(id PK, repository_id FK, run_id FK CASCADE, file_id FK→files CASCADE, name,
  qualified_name, kind CHECK, parent_id FK→symbols ON DELETE CASCADE, docstring, signature,
  parameters_json, return_annotation, bases_json, decorators_json, start_line, end_line,
  ordinal, UNIQUE(run_id, file_id, qualified_name, kind, ordinal))` + indexes
  `(repository_id, run_id)`, `(run_id, name)`, `(file_id)`, `(run_id, kind)`
- `relationships(id PK, repository_id FK, run_id FK CASCADE, source_file_id FK→files
  CASCADE, source_symbol_id FK→symbols SET NULL, rel_type CHECK, source_line, source_col,
  target_symbol_id FK→symbols SET NULL, target_ref, call_expr, resolution_status CHECK,
  evidence, UNIQUE(run_id, id))` + indexes `(run_id, rel_type)`, `(run_id,
  source_symbol_id)`, `(run_id, target_symbol_id)`
- `analysis_errors(id PK, run_id FK CASCADE, repository_id FK, file_id FK→files SET NULL,
  severity CHECK, category, message, rel_path, line, offset)` + index `(run_id)`

Notes:

- `UNIQUE(run_id, id)` makes the deterministic `rel_id` the in-run dedupe key: the same
  call site extracted twice cannot insert twice.
- `symbols.parent_id` self-FK with ON DELETE CASCADE expresses lexical containment.
- Source **text is not stored** in SQLite (decision AD-8); only the SHA-256 content hash,
  size, and parse metadata. The source endpoint re-reads from disk with strict boundary
  validation (§9). If the file changed on disk since analysis, the API reports the stored
  hash mismatch honestly (`content_changed_since_analysis: true`).

### Re-analysis model (AD-2): versioned runs + atomic detail swap

- Every trigger creates a new `analysis_runs` row (`running`).
- The pipeline computes the complete new fact set **in memory** first.
- Persistence of files/symbols/relationships and the deletion of all detail rows of
  *older* runs for that repository happen in **one transaction**, then the run is flipped
  to `completed` in the same transaction. Commit is the atomic switch.
- If anything fails before commit: the transaction rolls back; the previous completed
  run's data is untouched; the new run is marked `failed` with the error message stored
  in `analysis_runs.error` by a small separate transaction. `analysis_errors` rows are
  written only for a run that reaches a committed, completed state (a failure rolls
  them back); run history (`analysis_runs`) is always preserved.
- Consequence (documented): only the latest completed run's file/symbol/relationship
  details are queryable; older runs keep their summary + errors but not their full detail
  sets. This bounds storage and eliminates stale graph elements by construction.

## 6. Ingestion

### Discovery (`ingestion/discovery.py`)

- `os.walk(root, topdown=True, followlinks=False)` with `dirnames`/`filenames` sorted at
  every level ⇒ deterministic order.
- Directory pruning: names in the exclusion set (`ingestion/filters.py`: `.git`, `.hg`,
  `.svn`, `.tox`, `.nox`, `.venv`, `venv`, `env`, `__pycache__`, `node_modules`, `build`,
  `dist`, `*.egg-info`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.eggs`,
  `site-packages`, `htmlcov`, `.idea`, `.vscode`, `pip-wheel-metadata`) are pruned and
  recorded with reason `excluded_directory`. Exclusions are configurable via settings.
- **Directory links**: directory symlinks and Windows directory junctions (reparse
  points, which `Path.is_symlink()` does *not* report) are never traversed
  (recorded `symlinked_directory`).
  File links are read only if the *resolved real path* stays inside the resolved root;
  otherwise rejected with `symlink_escapes_root`.
- File candidates: `*.py` only. Oversized files (size > `max_file_bytes`) rejected with
  `file_too_large` and recorded. Discovery stops at `max_files` and sets `truncated=true`
  (recorded as a `limit_exceeded` issue).
- Containment helper: `is_within_root(root, candidate)` via `Path.resolve()` +
  `relative_to` — applied to symlinks and at API source retrieval.

### Reading (`ingestion/reader.py`)

- Read raw bytes; hash SHA-256; decode: BOM sniff (UTF-8 → `utf-8-sig`; UTF-16 LE/BE →
  `utf-16`, which consumes the BOM and derives byte order from it) else strict UTF-8.
  UTF-16 *without* a BOM is not detected and is treated as UTF-8 (documented in
  `docs/limitations.md`). Invalid encoding ⇒ per-file `read_error`, never fatal.
- Defense-in-depth size check inside the reader as well.

## 7. Analysis

### Parsing (`analysis/python_parser.py`)

`ast.parse(source)` per file. `SyntaxError` becomes a `ParseError(message, line, offset)`;
the pipeline records the failure on the file row (`parse_status='parse_error'`) plus an
`AnalysisIssue`, and continues with other files. One bad file never aborts a run.

### Symbol extraction (`analysis/symbols.py`)

Recursive statement walk with an explicit scope chain (module → class → function → …):

- Kinds: `module` (one per file), `class`, `function`, `method` (function whose direct
  parent is a class), plus nested functions/classes as children (parent-linked).
- Per symbol: name, qualified name, ordinal, docstring (`ast.get_docstring`), signature
  string and structured parameters (posonly/args/vararg/kwonly/kwarg, annotations and
  defaults via `ast.unparse`), return annotation, base classes (`ast.unparse`), decorator
  expressions (`ast.unparse`), one-based `start_line`/`end_line` (decorators included in
  the function's start line range).
- Nothing is invented: absent annotations/decorators/docstrings stay `None`.

### Import & call extraction (`analysis/imports_calls.py`)

One AST pass per file using a parent map for owner attribution:

- Imports: `import a`, `import a.b as c`, `from m import x`, `from m import x as y`,
  `from m import *`, relative (`level ≥ 1`), including imports nested inside functions.
  Each record: raw module, level, kind, alias(es), line/col, owning symbol.
- Calls: every `ast.Call`. Classified as `name` (`f()`), `attribute` (`a.b.c()`, storing
  root + attribute chain), or `indirect` (`f()()`, `d[k]()`, etc.). Original expression
  text preserved (`ast.unparse` of the callee) plus line/col and owning symbol.

### Conservative static resolution (`analysis/resolver.py`)

Two passes over the whole repository fact set.

**Pass 1 — module index and bindings.** Module name ← rel path (`pkg/m.py → pkg.m`,
`pkg/__init__.py → pkg`). Relative imports resolved against the importer's package
(level-aware, beyond-top-level ⇒ `unresolved`). Per file, an alias→binding table:
`import a.b as c` ⇒ module binding; `from m import x as y` ⇒ symbol binding; wildcard
flag recorded. Import status assignment:

- internal module found ⇒ `resolved_static` (target file recorded);
- stdlib/builtin top-level component (`sys.stdlib_module_names`, `sys.builtin_module_names`)
  ⇒ `external` (evidence: "standard library");
- other absolute unresolved ⇒ `external` (evidence: "not present in repository;
  third-party or unavailable");
- relative import that cannot be located ⇒ `unresolved`.

**Pass 2 — call resolution** (owner scope chain = enclosing functions + module):

1. Call root bound as parameter/local in any enclosing scope ⇒ **not resolved**:
   `unresolved` (parameter) or `dynamic_or_unknown` (other local binding). Evidence kept.
2. `self.m()` / `cls.m()` inside a method ⇒ method lookup on the enclosing class, then its
   statically resolved bases (same-repo classes) ⇒ `resolved_static` if found, else
   `unresolved`.
3. `Cls.m()` where `Cls` is a same-file/imported class ⇒ method lookup as above.
4. `mod.f()` / `pkg.mod.f()` where the root is a module-binding import alias ⇒ symbol
   lookup in the target file ⇒ `resolved_static`, else `unresolved`.
5. Bare `f()` ⇒ enclosing-file module-level functions/classes (also reachable via symbol
   bindings through imports) ⇒ `resolved_static`; builtins ⇒ `external` (`builtin:f`);
   wildcard-import present and nothing else matched ⇒ `ambiguous`; otherwise `unresolved`.
6. Any other receiver (`obj.m()` with unknown `obj`) ⇒ `unresolved` with evidence
   "receiver type unknown"; `indirect` call forms ⇒ `dynamic_or_unknown`.
7. Inheritance: base expression resolved by the same binding rules ⇒ `resolved_static`
   (target class recorded) or `unresolved`; nothing guessed.

`resolved_static` is only ever assigned with a concrete in-repository target symbol and a
recorded evidence string naming the rule. Uncertainty is never disguised.

## 8. Graph semantics (`repolens.graph`)

NetworkX `DiGraph`, built from persisted facts (same builder validates the fresh pipeline
outcome in-memory before persistence).

**Nodes** (ids are the domain ids):

| Type | Id | Source |
|---|---|---|
| `repository` | `repo_` + repository id | registered repository |
| `file` | `file_*` | one per analyzed `.py` file; **also represents the module** (AD-4: module ≡ file node; carries `module_name`; module symbols are not separate nodes) |
| `class`, `function`, `method` | `sym_*` | non-module symbols |

**Edges** (directed; each carries `sites` provenance: list of `{line, symbol?, evidence?}`):

- `contains`: repo→file, file→top-level symbol, symbol→nested symbol.
- `imports`: file→file, only `resolved_static` imports (site lines deduped).
- `calls`: symbol→symbol, only `resolved_static` calls (multiple call sites merge into
  one edge with a growing `sites` list — no duplicate edges).
- `inherits_from`: class→class, only `resolved_static` bases.

**Unresolved / external / ambiguous / dynamic relationships are never graph edges.** They
exist as `RelationshipRecord` rows in SQLite and are fully retrievable through
`GET /api/repositories/{id}/relationships` with status filters (decision AD-3: records
without fabricated nodes). The alternative (placeholder target nodes) was rejected
because it fabricates entities that do not exist in the source.

**Queries** (`graph/queries.py`): node lookup; neighbors with edge-type and direction
filters; edge listing with type filter; bounded shortest path (iterative BFS, default
depth cap 6 / max 10, visited-node cap 20 000 — returns `found=false` with a reason, never
a fabricated path); repository subgraph stats.

**Integrity validation** (run in-pipeline and on DB load): every edge endpoint must exist;
every symbol's parent must exist in the same repo/file; every relationship's source file
must exist; any violation raises `GraphIntegrityError` and fails the run (test-asserted).

## 9. Security boundary (`docs/security.md` has the full statement)

- Registration paths must exist and be directories; they are resolved once, stored, and
  used as the trust root.
- All file access stays inside the resolved root: symlink policy + containment checks in
  discovery and again at source retrieval (which also rejects `..` segments and
  absolute-looking rel paths, and validates against the DB-recorded rel path).
- Configurable limits: `max_file_bytes` (default 1 MB), `max_files` (default 5 000),
  graph path bounds.
- No execution: no `subprocess`, no `import`/`__import__` of analyzed code, no
  `runpy`, no plugin/entry-point loading from analyzed repos. A meta-test
  (`tests/unit/test_no_execution.py`) greps the `repolens` package source for
  forbidden APIs as a regression guard.
- API errors: domain exceptions mapped to status codes with safe messages; no stack traces
  or absolute host paths leaked in responses (root path is only ever returned to the
  operator who registered it — kept out of file/symbol/graph responses entirely).
- Phase 1 is a single-user local tool: no auth layer; documented explicitly.

## 10. Analysis summary (computed, never fabricated)

`AnalysisSummary` counts, all derived from the actual outcome:
`files_discovered, files_excluded, excluded_directories, files_read, files_parsed,
files_read_errors, files_parse_errors, symbols_extracted, relationships_extracted,
relationships_resolved, relationships_unresolved, relationships_external,
relationships_ambiguous, relationships_dynamic, warnings, errors, duration_seconds`.

## 11. API surface (contracts)

All JSON; list endpoints accept `limit` (default 50, max 500) and `offset ≥ 0`.

| Method & path | Purpose | Key responses |
|---|---|---|
| `GET /health` | liveness | `{"status":"ok"}` |
| `POST /api/repositories` | register `{name, path}` | 201 `Repository`; 400 invalid path; 409 duplicate |
| `GET /api/repositories` | list | `list[Repository]` |
| `GET /api/repositories/{rid}` | metadata + latest run summary | `Repository`; 404 |
| `POST /api/repositories/{rid}/analysis` | synchronous analysis | 201 `AnalysisRun`; 404; 500 run recorded failed |
| `GET /api/repositories/{rid}/runs` | run history | `list[AnalysisRun]` |
| `GET /api/analysis/runs/{run_id}` | run summary | `AnalysisRun`; 404 |
| `GET /api/analysis/runs/{run_id}/errors` | errors/warnings | paginated `AnalysisIssue` |
| `GET /api/repositories/{rid}/files` | indexed files | paginated `FileRecord` |
| `GET /api/repositories/{rid}/files/{rel_path:path}` | file metadata; `?include_source=&start_line=&end_line=` source slice | `FileDetail`; 400 traversal; 404 |
| `GET /api/repositories/{rid}/relationships` | imports/calls/inheritance w/ status filter | paginated `RelationshipRecord` |
| `GET /api/repositories/{rid}/symbols` | filters: `file_id`, `kind`, `name` | paginated `SymbolRecord` |
| `GET /api/symbols/{symbol_id}` | symbol detail | `SymbolRecord`; 404 |
| `GET /api/symbols/{symbol_id}/parent` / `/children` | lexical relations | 404 / list |
| `GET /api/repositories/{rid}/graph/stats` | node/edge counts by type | `GraphStats` |
| `GET /api/repositories/{rid}/graph/nodes` | nodes (`?type=`) | paginated `GraphNode` |
| `GET /api/repositories/{rid}/graph/edges` | edges (`?type=`) | paginated `GraphEdge` |
| `GET /api/graph/nodes/{node_id}` | single node detail | `GraphNode`; 404 unknown node |
| `GET /api/graph/nodes/{node_id}/neighbors` | neighbors (`?edge_type=&direction=&limit=`) | `NeighborResult`; 404 unknown node |
| `GET /api/graph/path?source=&target=&max_depth=` | bounded path | `PathResult` (found=false + reason when none); 400 bounds |

Error body: `{"error": {"code": "...", "detail": "..."}}` via domain-exception handlers.
OpenAPI served by FastAPI at `/docs` and `/openapi.json`.

## 12. Configuration (`repolens.config`)

Frozen dataclass `Settings`, `Settings.from_env()` (decision AD-11: plain env reads, no
pydantic-settings dependency — fewer moving parts, trivially injectable in tests):

| Env var | Default |
|---|---|
| `REPOLENS_DB_PATH` | `repolens.db` |
| `REPOLENS_MAX_FILE_BYTES` | `1000000` |
| `REPOLENS_MAX_FILES` | `5000` |
| `REPOLENS_PATH_MAX_DEPTH` | `6` (hard cap 10) |
| `REPOLENS_PATH_MAX_NODES` | `20000` |
| `REPOLENS_TRACE_MAX_DEPTH` | `4` (hard cap 8) |
| `REPOLENS_TRACE_MAX_NODES` | `500` (hard cap 5 000) |
| `REPOLENS_SEARCH_MAX_FILES` | `2000` |
| `REPOLENS_SEARCH_MAX_HITS` | `1000` |
| `REPOLENS_COMPLEXITY_WARNING_THRESHOLD` | `10` |
| `REPOLENS_FAN_OUT_WARNING_THRESHOLD` | `10` |
| `REPOLENS_FAN_IN_WARNING_THRESHOLD` | `10` |
| `REPOLENS_LOG_LEVEL` | `INFO` |

Run: `python -m repolens.main` (uvicorn, configurable host/port env
`REPOLENS_HOST`/`REPOLENS_PORT`, defaults `127.0.0.1:8000`). Tests: `python -m pytest`.
Lint: `ruff check .` + `ruff format --check .`. Types: `mypy repolens` (best-effort,
reported honestly).

## 13. Testing strategy

- **Unit** (`tests/unit`): every component in isolation with temp dirs/DBs — discovery,
  exclusions, symlink/containment, limits, reader encodings, parser failures, symbol
  extraction incl. nesting/collisions/lines, import & call extraction, all resolver
  statuses, graph build/integrity/dedupe, bounded queries, DB constraints, re-analysis
  transaction semantics, identifier stability, path-safety helpers, no-execution guard.
- **Integration** (`tests/integration`): fixtures A–D driven through the FastAPI app
  (`TestClient`) with an isolated temp database: register → analyze → assert files,
  symbols, imports, calls, edges, source locations → re-analyze (add/modify/delete/
  break a file) → assert no duplication, no stale data, honest failure handling →
  security probes (traversal, escaping symlinks, absolute paths).
- Deterministic (sorted iteration everywhere; UUIDs only for run ids), no network, no GPU,
  no execution of fixture code. Skips (e.g., symlink creation refused by Windows) are
  explicit and reported, never silent.

## 14. Architecture decisions (resolved)

- **AD-1** Deterministic hashed ids (§4); run ids are UUIDs.
- **AD-2** Versioned runs + atomic detail swap; only latest completed run keeps details (§5).
- **AD-3** Unresolved relationships: DB records without graph edges/nodes (§8).
- **AD-4** Module ≡ file node in the graph; module symbols exist in the symbol table for
  lexical parentage but are not separate graph nodes.
- **AD-5** Raw stdlib `sqlite3` with explicit DDL instead of SQLAlchemy: smaller dependency
  surface, full control of transactions/constraints, easier to audit for Phase 1 scale.
- **AD-6** Conservative resolution rules (§7); `resolved_static` requires an in-repo target.
- **AD-7** Symbol identity excludes line numbers; ordinal disambiguates redefinitions (§4).
- **AD-8** Source text not stored in DB; served on demand from disk with boundary +
  hash-mismatch reporting.
- **AD-9** Error taxonomy: per-file statuses + run-level issue list (§3).
- **AD-10** Pagination defaults 50/500 on every list endpoint.
- **AD-11** Settings via frozen dataclass + env vars.
- **AD-12** Encoding: BOM sniff UTF-8/16, else strict UTF-8; failures per-file.
- **AD-13** Symlinks: never traverse directory symlinks; file symlinks only inside root.
- **AD-14** Path queries: BFS with depth cap (default 6, max 10) and visited cap 20 000.

## 15. Phase 2 comprehension architecture

Phase 2 is layered on the Phase 1 facts without changing the ingestion,
parsing, symbol, resolution, graph, or storage contracts. It adds deterministic
analysis modules, services, routes, and one persisted table.

### 15.1 Module map

| Layer | Modules | Responsibility |
|---|---|---|
| Analysis | `analysis/function_metrics.py` | Per-function structural metrics from the AST |
| Analysis | `analysis/cfg.py` | Per-function control-flow graph and def/use node facts |
| Analysis | `analysis/dataflow.py` | Bounded intraprocedural reaching definitions and def-use |
| Analysis | `analysis/function_locator.py` | Locate a persisted symbol's function node in current source |
| Analysis | `analysis/entrypoints.py` | Explicit `__main__` guard / `__main__.py` detection |
| Analysis | `analysis/explanations.py` | Deterministic five-level explanation templates |
| Analysis | `analysis/search.py` | Lexical symbol/text/token matching, scoring, pagination |
| Analysis | `analysis/tracing.py` | Bounded forward/reverse/both relationship traversal |
| Analysis | `analysis/architecture.py` | Import graph, cycles, centrality, coupling, hotspots, findings |
| Services | `services/comprehension_service.py` | Symbol→file→source→CFG→dataflow→explanations orchestration |
| Services | `services/search_service.py` | Latest-run symbol loading + bounded current-source text search |
| Services | `services/trace_service.py` | Symbol/file trace loading, traversal, connect, dependents |
| Services | `services/architecture_service.py` | Latest-run facts → architecture summary and findings |
| API | `api/routes/metrics.py`, `cfg.py`, `explanations.py`, `search.py`, `trace.py`, `architecture.py` | Thin HTTP adapters, query validation, pagination |
| Persistence | `persistence/database.py` | Schema v2 + additive `function_metrics.symbol_id`/`statement_count` migration |

`api/deps.py` wires the services from the existing `AnalysisService`, database,
and frozen `Settings`. New domain errors (`source_unavailable`,
`analysis_not_available`, `invalid_request`) are mapped in `main.py` to 409, 404,
and 400 respectively.

### 15.2 On-demand comprehension pipeline

Only function metrics are persisted. CFG, data flow, explanations, search, trace
and architecture are computed when requested:

```text
persisted symbol / run facts
        │
        ├── metrics endpoint ──► function_metrics rows (persisted)
        │
        ├── CFG/dataflow/explanation
        │       symbol ──► file record ──► boundary-checked source read
        │              ──► ast.parse ──► locate function by line/name
        │              ──► build CFG ──► def/use facts ──► dataflow
        │              ──► deterministic explanation
        │
        ├── search
        │       stored symbols + current source of parsed latest-run files
        │
        ├── trace
        │       persisted relationships ──► resolved-static BFS
        │
        └── architecture
                persisted files/symbols/relationships/metrics
                ──► resolved-static import graph ──► cycles/centrality/coupling
```

This deliberately avoids a second source of truth. On-demand results report
`source_changed_since_analysis` when the current hash differs from the analyzed
hash, and `source_unavailable` when source cannot be read. Source is read through
the same containment checks as Phase 1; no endpoint returns an absolute host
path.

### 15.3 Persistence changes

Schema version is **2**. The only new persisted product is `function_metrics`:

| Column | Meaning |
|---|---|
| `id` | Autoincrement primary key |
| `repository_id`, `run_id`, `file_id` | Run/file association |
| `symbol_id` | Function symbol association (string, not a FK) |
| `qualified_name`, `start_line`, `end_line` | Source identity/range |
| `parameter_count`, `line_count`, `statement_count` | Size and own-scope statement count |
| `if_count`, `for_count`, `while_count`, `try_count`, `with_count`, `match_count`, `return_count`, `call_count` | Structural counts |
| `max_nesting_depth` | Control-flow nesting |
| `cyclomatic_complexity` | AST decision-point approximation |

Indexes cover `(run_id)`, `(repository_id, run_id)`, and `(symbol_id)`.
`symbol_id` is populated during `pipeline._assign_ids` after symbol IDs exist.
The additive migration adds the column to databases created before Phase 2; it
does not rewrite existing rows. Metric rows follow the Phase 1 atomic detail-swap
and latest-completed-run rules, so old metrics never leak into a new run and a
failed re-analysis leaves the previous completed run queryable.

### 15.4 Determinism and bounds

- Every traversal uses sorted frontiers / source order; IDs are stable hashes or
  deterministic sequences (`n0…`, `d0…`, `u0…`).
- Cycles are canonically rotated and sorted before evidence is emitted.
- Search ties break on path, line, kind, qualified name and snippet.
- Architecture findings sort by severity, kind, subject and id.
- Every analysis cap that can omit results (CFG nodes/edges/depth, data-flow
  definitions/uses/iterations, trace depth/nodes, search files/hits, cycle
  count/length, findings) sets an explicit `truncated` flag and/or note. A cap is
  not evidence of absence. Display-only top-N lists (central components,
  hotspots, coupling rows) are documented response shapes, not completeness
  claims.
- Analysis never imports, executes, or mutates analyzed code. The E2E fixture
  writes a marker only if executed; the test asserts it is absent.

### 15.5 Phase 2 decisions

- **AD-15** Persist only function metrics; derive CFG/data flow/explanations/
  search/trace/architecture on demand. This trades repeated computation for no
  stale derived state and a smaller schema.
- **AD-16** Cyclomatic complexity remains an AST decision-point approximation,
  not a CFG number, and is never a defect verdict.
- **AD-17** CFG exception edges are conservative (try-body nodes → every
  handler); exception types and `finally` routing for `return`/`break`/`continue`
  are explicitly not modelled.
- **AD-18** Data flow is local may-reaching-definitions only. No interprocedural,
  alias, points-to or whole-program claims.
- **AD-19** Tracing follows only `resolved_static` relationships and reports all
  other statuses; a trace is a static relationship path, not a runtime trace.
- **AD-20** Architecture uses resolved-static imports only. Degree
  centrality/coupling are structural indicators; cycles are objective facts but
  not defect labels.
- **AD-21** Search is lexical and deterministic; no embeddings or semantic
  similarity. Text search reads current source only for parsed latest-run files
  and reports truncation.
- **AD-22** Explanations separate facts, heuristics, unknowns and evidence.
  Templates never invent intent; missing/approximate results are stated.
