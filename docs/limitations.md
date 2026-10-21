# RepoLens — Limitations (Phase 1 and Phase 2)

Status: honest, complete list of what RepoLens does **not** do. This document is
deliberately conservative: if a capability is partial or uncertain, it is stated
here rather than over-promised elsewhere in the docs.

The Phase 1 limitations remain in force. Phase 2 adds deterministic
comprehension features on top of the same source facts; §13 states their
specific limits and non-goals.

## 1. Scope of language and analysis

- **Python only.** Only `.py` files are discovered and parsed. `.pyi` stubs,
  Cython (`.pyx`), Jupyter notebooks, and any other language are ignored with no
  warning in Phase 1. Non-`.py` files are simply not candidates.
- **Static analysis only.** No code is run, so no runtime information is
  collected: no dynamic dispatch resolution, no monkey-patch awareness, no
  executed-path evidence, no coverage or profiling data. Runtime observations
  are documented as *absent*, not simulated.
- **One interpreter's grammar.** Parsing uses the running interpreter's `ast`
  (CPython 3.13 here). Source using syntax newer than the interpreter produces a
  per-file `parse_error`; such files contribute no symbols or relationships. The
  file and its error are still recorded.
- **Version-awareness is limited.** There is no per-file target-version handling
  (no `sys.version_info` gating), so constructs valid in one Python version may
  parse differently or fail under this interpreter.

## 2. Conservative resolution (intentional incompleteness)

Resolution is deliberately conservative (decision AD-6). `resolved_static` is
only ever assigned when a **concrete in-repository target symbol** is found and
an evidence string naming the rule is recorded. Consequences:

- **Dynamic dispatch is not resolved.** `obj.method()` where the type of `obj`
  is unknown is `unresolved` (evidence: receiver type unknown); indirect call
  forms (`f()()`, `d[k]()`) and `getattr`-based dispatch are
  `dynamic_or_unknown`. The call graph therefore excludes these edges by design.
- **Attribute calls on parameters/locals are not inferred.** No type inference,
  no data-flow analysis, no points-to analysis. A call whose target depends on a
  value's runtime type cannot be resolved.
- **Star imports produce `ambiguous`, not guesses.** If `from m import *` is
  present and a bare name cannot otherwise be matched, the relationship is
  `ambiguous`. Names brought in only via a wildcard are not resolved to concrete
  symbols.
- **External code is never resolved.** Standard-library and third-party imports
  are marked `external`; the system does not read installed packages, so calls
  into libraries have no in-repo target. Builtins are recorded as `external`
  (e.g., `builtin:len`).
- **Duplicate definitions are not resolved to a single binding.** When the same
  qualified name is defined more than once, every relationship that would
  resolve to it is reported as `ambiguous` (never `resolved_static`), because
  the model does not track execution order or control flow. The distinct
  definitions retain distinct identities and source locations.
- **No cross-repository resolution.** Each repository is analyzed in isolation;
  a call into another registered repository is not linked.
- **Monkey-patching, decorators that rebind names, and metaprogramming are not
  modeled.** A decorator is recorded as a fact (its expression), but its runtime
  effect on the decorated name is not simulated.
- **Conditional definitions are taken at face value.** Two `def f` at module
  level under mutually exclusive `if` branches both become symbols (ordinal
  disambiguates them); which one "wins" at runtime is not decided.

The result is a graph that is **sound with respect to what it claims** (every
`resolved_static` edge has an in-repo target and named evidence) but
**incomplete**: it intentionally omits edges it cannot justify. Users should not
treat absence of an edge as proof the call does not happen.

## 3. Symbol identity and stability

- Symbol IDs are deterministic hashes over `(repo, rel_path, kind,
  qualified_name, ordinal)`; **line numbers are not part of identity**
  (decisions AD-1, AD-7). IDs are therefore stable across edits that do not
  change a symbol's file, kind, name, or enclosing scope chain.
- **IDs change when:** the symbol is renamed, moved to another scope/file,
  changes kind, **or when an earlier same-named duplicate is added/removed**
  (which shifts `ordinal`). Adding a duplicate definition earlier in a file can
  therefore renumber later duplicates.
- `qualified_name` is derived from the module path and lexical scope chain. It
  reflects *lexical* structure, not runtime binding; conditional or reassigned
  definitions still get names based on where they textually appear.
- No attempt is made to reconcile the "same" logical symbol that is defined in
  multiple places; each definition is a distinct symbol.

## 4. Import and call extraction

- Imports are recorded, not followed. Re-exports, `__all__`, and package
  `__init__` re-export chains are not traversed to find a symbol's "real"
  definition.
- Relative imports beyond the top-level package (level that walks above the
  repository root) are `unresolved`.
- Namespace packages and complex `src` layouts are not specially handled; a
  module's identity is its path-derived dotted name relative to the registered
  root. If the root is not the import root (e.g., a `src/` layout is registered
  one level too high or too low), module names may not match how Python would
  import them.
- `import a.b.c` binds only the root name (`a`) in the alias table; deep
  attribute access through the intermediate modules is not fully modeled for
  every call shape.

## 5. Graph semantics

- **Unresolved, external, ambiguous, and dynamic relationships are never graph
  edges** (decision AD-3). They exist as database records and are retrievable via
  the relationships endpoint, but they do not appear as nodes or edges. The graph
  shows only structural `contains`, resolved `imports`, resolved `calls`, and
  resolved `inherits_from`.
- **Module ≡ file node** (decision AD-4): module symbols are not separate graph
  nodes. A "module" reference is represented by the file node.
- **The graph is rebuilt from SQLite per request** for graph endpoints. There is
  no cached, incrementally-updated graph; each graph query pays the
  reconstruction cost. Acceptable for Phase 1 scale (bounded by
  `REPOLENS_MAX_FILES`), but a limitation at larger scale.
- **Directed, not weighted.** Edges carry call-site provenance (`sites`) but no
  weights; queries do not rank or prioritize edges.
- **Path queries are bounded** (depth default 6 / hard cap 10; visited cap
  20 000). When a bound is hit, the result is `found=false` with a reason; a
  path is never fabricated. A real path longer than the depth cap is reported as
  not found — this is a bound, not a claim of nonexistence.

## 6. Only the latest completed run keeps details

- Versioned runs keep **run history and errors**, but only the **latest
  completed** run's files/symbols/relationships are queryable (decision AD-2).
  Older runs' full detail sets are deleted at the atomic swap to bound storage
  and eliminate stale data.
- Querying prior-run details is therefore not supported in this release.
- A `running` or `failed` run has no queryable detail set.

## 7. Analysis execution model

- **Synchronous, single-process, no queue.** A trigger blocks until the run
  finishes; there is no background worker, no timeout, and no cancellation.
  Large (but legal) repositories occupy the request for the run's duration.
- **No concurrency control across registrations.** Two triggers for the same
  repository are serialized only by SQLite's write locking, not by an application
  lock; heavy concurrent triggering is not a current target.
- **No incremental analysis.** Every run re-reads and re-parses every discovered
  file from scratch; unchanged files are not skipped via content hash (the hash
  is stored for change *reporting*, not for incremental reuse).
- **No scheduling or filesystem watching.** Analysis happens only when
  explicitly triggered.

## 8. Storage, persistence, and encodings

- **Source text is not stored** (decision AD-8); it is re-read from disk on
  demand. If the file changed or was deleted since analysis, the endpoint reports
  `content_changed_since_analysis` or `source_available=false` honestly — but it
  does **not** return the analyzed-at-the-time content (that snapshot is not
  retained).
- **Encoding support is bounded:** BOM-sniffed UTF-8 / UTF-16 LE / UTF-16 BE,
  else strict UTF-8. Other encodings (Latin-1, Shift-JIS, etc.) become per-file
  `read_error` and contribute nothing. **UTF-16 files without a BOM are not
  detected** — they are decoded as strict UTF-8 (which can succeed while leaving
  embedded NUL bytes, producing a `parse_error`) and are effectively unsupported.
- **SQLite single-file database.** No replication, no server, no concurrent
  multi-writer story beyond SQLite's own locking. Schema migrations are limited
  to idempotent additive migrations guarded by `schema_meta.schema_version`; no
  general migration framework exists.
- **Thread-local connections** are not pooled; extreme thread counts create many
  connections.

## 9. Platform and environment

- **Windows symlink caveat.** Creating symlinks may require elevation or
  Developer Mode. The discovery symlink policy is implemented and runs in
  production, but the two creation-based symlink tests **skip** on hosts that
  refuse `os.symlink`. On such hosts the escape-defense code is exercised by
  `validate_rel_path`/`is_within_root` tests, not by an actual symlink; this is
  disclosed in the final audit.
- **Path handling is normalized to POSIX relative paths.** Windows case
  insensitivity and drive letters are resolved at registration, but a repository
  whose files are reached through differing case may behave unexpectedly.
- **Filesystem races.** Discovery stats a file, then the reader reads it; a file
  changed between those steps is read as-is (its on-disk hash is used). This is a
  benign race, not a correctness guarantee.

## 10. API and operations

- **No authentication, authorization, TLS, or rate limiting.** Intended for
  local, single-user use only (see `docs/security.md` §10). Must not be exposed
  to an untrusted network as-is.
- **No pagination cursors.** List endpoints use `limit`/`offset` with default 50
  / max 500; very large result sets require manual paging.
- **No API versioning beyond the FastAPI `/api` prefix** and no deprecation
  policy yet.
- **No structured run progress.** A trigger returns the completed run (or an
  error); there is no streaming/polling progress for an in-flight analysis.

## 11. Testing and quality gates (honest statement)

- The suite passes **252 tests with 2 skipped** on the development host
  (CPython 3.13, Windows); the 2 skips are the symlink-creation tests above.
- `ruff` and `mypy` are configured and clean on the current tree; mypy is
  best-effort type checking, not a proof of correctness.
- Tests are deterministic and offline (no network, no GPU); they do not exercise
  truly massive repositories, OS-level concurrent triggers, or network exposure,
  because those are outside the supported scope.

## 12. Explicit non-goals for Phase 1

Intentionally **not** present, to avoid scope creep (and to keep the "no LLM, no
ML" constraint unambiguous):

- No LLMs, embeddings, or ML of any kind.
- No natural-language summaries or generated code.
- No multi-language support.
- No runtime instrumentation, no test execution, no dependency installation.
- No distributed/multi-tenant operation.
- No IDE/editor integration.

## 13. Phase 2 comprehension limitations

### 13.1 Function metrics

- Cyclomatic complexity is an **AST decision-point estimate**
  (`1 + if + for + while + except handlers + match cases + extra boolean
  operands`). It is **not** a CFG cyclomatic number, a path count, or a defect
  prediction.
- Nested function/class/lambda bodies are excluded from the enclosing
  function's counts. A very large nested function therefore does not make its
  parent look large.
- `max_nesting_depth` covers `if`/`for`/`while`/`try`/`with`/`match` only.
  `try*` is treated like `try` for counting and nesting. Comprehension nesting
  and boolean short-circuit depth are not separate nesting levels.
- Metrics are per function/method; module-level statements have no function
  metric record.
- The estimate counts syntactic decisions, not runtime iterations, short
  circuits, or exception paths.

### 13.2 Control-flow graphs

- The CFG is a **structural model**, not a Python bytecode CFG. It is built from
  the current source on demand, not persisted from the analyzed snapshot.
- Exception edges are deliberately conservative: every node created inside a
  `try` body links to every handler. Exception types, implicit propagation,
  handler specificity, and exception re-raise semantics are not modelled.
- `return`, `break`, and `continue` inside `try` do **not** route through
  `finally`. The `finally` join models normal completion and handled exception
  paths only.
- `match` exhaustiveness is approximated. An unguarded wildcard/name/`_` case is
  treated as exhaustive; guards are not evaluated, so a guarded wildcard is not
  assumed to match. Non-exhaustive matches get a `no_match` edge.
- Statements after an unconditional terminator (`return`, `raise`, `break`,
  `continue`) are unreachable and are omitted with a note; they are not
  represented as if they could execute.
- Nested definitions are represented by one `nested_def` node and are not
  inlined into the enclosing CFG.
- Graphs are bounded (default 5,000 nodes, 20,000 edges, depth 200). A capped
  graph is explicitly `truncated`; it is not a claim that the omitted structure
  does not exist.
- Source changes since analysis are reported. If the source is deleted or
  unreadable, CFG/data-flow return `source_unavailable` rather than stale facts.

### 13.3 Local data flow

- Analysis is **intraprocedural only**: it does not follow calls, returns,
  closures, module globals, class attributes, or dynamic dispatch.
- There is **no alias or points-to analysis**. `a = b; a.x = 1` does not update
  `b.x`; container contents and object fields are not tracked.
- Lambda bodies and comprehension interiors are separate scopes and are not
  analysed. The first iterable of a comprehension and lambda defaults are
  collected at the enclosing scope because Python evaluates them there.
- `undefined_uses` means "no locally reaching definition". It is **not** a
  runtime-error claim: the name may be a global, builtin, closure variable,
  comprehension variable, or imported via a form the local analysis does not
  model.
- `global`/`nonlocal` names are reported separately and suppress the
  `undefined_uses` marker; assignments to those names are still modelled as
  definitions in the local flow for consistency with the persisted facts.
- Reaching definitions are a **may** analysis over the CFG. Conservative
  exception edges can make extra definitions appear to reach a use; this is
  intentional over-approximation, not a runtime guarantee.
- Definitions/uses are bounded (default 5,000 definitions, 10,000 uses, 50,000
  fixpoint iterations). Truncation is explicit.
- The analysis never claims whole-program values or that a variable is
  uninitialized at runtime.

### 13.4 Explanations

- Explanations are deterministic templates over stored facts, metrics, CFG, data
  flow and relationships. They do **not** infer programmer intent, quality, or
  business meaning.
- A "fact" is only as good as the underlying approximation. CFG and data-flow
  facts inherit §13.2 and §13.3.
- Heuristics are explicitly labelled. For example, a complexity warning is a
  review prompt, not a defect verdict.
- `unknowns` reports missing parse data, missing metrics, truncation, source
  changes, and scope limits. A missing explanation is not filled with invented
  prose.
- File explanations can use stored metadata when source is unavailable; CFG and
  data-flow explanations cannot and return `source_unavailable`.
- Statement explanations use the current source and prefer a real statement node
  over synthetic `entry`/`exit`/`merge` nodes. A blank line with no
  source-backed node returns an unknown rather than a fabricated statement.

### 13.5 Search

- Search is **lexical only**. There are no embeddings, semantic similarity,
  stemming, fuzzy matching, ranking by runtime importance, or natural-language
  understanding.
- `all` combines symbol matching with case-insensitive substring text search;
  `symbol`/`identifier` do not read source; `text`/`token` read current source
  for parsed files in the latest completed run.
- Text search does not search deleted files, unreadable files, or files that
  were excluded from the run. Parse-error files have no symbols and are excluded
  from text search.
- Empty/whitespace queries are rejected; a missing `q` is a validation error.
  No results is an empty page, not an error.
- Text reading is bounded (`search_max_files`, per-file size, 50 hits per file)
  and combined hits are capped (`search_max_hits`). Truncation is reported.
- Ranking is a documented integer heuristic. Ties break deterministically on
  path, line, hit kind, qualified name and snippet. A high score is not a claim
  of semantic relevance.
- Source text may have changed since analysis. Search reports the current text
  and does not pretend it is the analyzed snapshot.

### 13.6 Tracing

- Only `resolved_static` relationships with a concrete in-repository target are
  traversed. `unresolved`, `external`, `ambiguous`, and `dynamic_or_unknown`
  references are reported at the originating node and never followed.
- A trace is a **static relationship path**, not a runtime call stack, execution
  trace, or proof that a call happens. Absence of a path is bounded and
  explicitly reported; it is not proof of nonexistence.
- Traversal is bounded (`max_depth` 1–8, `max_nodes` 1–5,000). Hitting a cap sets
  `truncated` and a note.
- Cycles are reported in traversal order. Bidirectional traversal suppresses the
  immediate reverse of the edge just traversed to avoid inventing two-node
  cycles. Architecture import cycles are the authoritative cycle report.
- File dependents are a reverse **import** trace mapped through resolved import
  targets. Calls and inheritance are not file dependency edges for this view.
- A file without a module symbol (for example a parse-error file) cannot be used
  as a dependents root and returns `file_not_found`.

### 13.7 Architecture indicators

- The dependency graph uses **resolved-static imports only**. Calls are not
  module dependencies; inheritance is not counted as an import.
- Multiple imports of the same target file collapse to one edge; the earliest
  `(line, evidence)` is retained.
- Cycles are bounded simple cycles (`max_cycles` 50, `max_cycle_length` 10) and
  canonicalized for deterministic evidence. A cycle means modules depend on each
  other; it does **not** mean the code is wrong.
- Degree centrality is `(in_degree + out_degree) / (N - 1)` for `N > 1`, else 0.
  It measures graph connectivity, not importance or quality.
- Instability is `fan_out / (fan_in + fan_out)` when the denominator is non-zero,
  else `null`. It is a coupling indicator, not a stability guarantee.
- Complexity hotspots use the §13.1 AST estimate. A hotspot is a review prompt,
  not a defect.
- Entry points are only explicit `if __name__ == "__main__"` guards and
  `__main__.py` files, scanned within a bounded file limit. Framework entry
  points, console scripts, and implicit imports are not discovered.
- Findings are capped and sorted; `truncated=true` means more findings exist.
  No metric or graph shape alone labels code defective.

### 13.8 Persistence and API

- Only function metrics are persisted for Phase 2. CFG, data flow, explanations,
  search, trace and architecture are computed on demand from persisted facts plus
  bounded current-source reads. This avoids stale derived state but means those
  endpoints pay computation cost per request.
- Function metrics are associated with a `symbol_id`, but the column is an
  association string, not a foreign key. Symbol reanalysis can change IDs; old
  metric rows are removed with the old detail set.
- Only the latest **completed** run's details are queryable. A failed or running
  run does not replace the previous completed run.
- On-demand results use current source. If source changed, results report the
  change. CFG, data-flow, function-explanation and statement-explanation
  endpoints return `source_unavailable` when the required current source is gone;
  file explanations report the missing source as an unknown, while search and
  architecture simply skip unreadable files. There is no retained source
  snapshot from analysis time.
- Unknown repositories return 404 for Phase 2 explanation/search/trace/
  architecture endpoints, while Phase 1-style list endpoints (including metrics)
  return an empty page. A repository without a completed run returns
  `analysis_not_available` for the Phase 2 endpoints.
- Cross-repository symbol IDs are rejected rather than leaking facts from another
  repository.
- The API remains single-user, synchronous, unauthenticated, and local. No
  background workers, queues, cancellation, or incremental analysis were added.

### 13.9 Phase 2 non-goals

The following are intentionally absent, not missing work packages:

- interprocedural data flow, alias/points-to analysis, whole-program values;
- runtime traces, coverage, profiling, dynamic dispatch, or test execution;
- embeddings, vector search, semantic similarity, LLM explanations, or generated
  prose;
- automatic defect classification or quality scores from metrics;
- executing, importing, or mutating analyzed code;
- modeling Python exception types/finalization beyond the conservative CFG
  approximations;
- IDE integration, multi-language support, or distributed/multi-tenant
  operation.
