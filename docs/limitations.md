# RepoLens — Limitations (Phase 1)

Status: honest, complete list of what Phase 1 does **not** do. This document is
deliberately conservative: if a capability is partial or uncertain, it is stated
here rather than over-promised elsewhere in the docs.

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
- Querying prior-run details is therefore not supported in Phase 1.
- A `running` or `failed` run has no queryable detail set.

## 7. Analysis execution model

- **Synchronous, single-process, no queue.** A trigger blocks until the run
  finishes; there is no background worker, no timeout, and no cancellation.
  Large (but legal) repositories occupy the request for the run's duration.
- **No concurrency control across registrations.** Two triggers for the same
  repository are serialized only by SQLite's write locking, not by an application
  lock; heavy concurrent triggering is not a Phase 1 target.
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
  multi-writer story beyond SQLite's own locking. Schema migrations are only
  *hooked* (`schema_meta.schema_version`); no migration tooling exists yet.
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

- The suite passes **139 tests with 2 skipped** on the development host
  (CPython 3.13, Windows); the 2 skips are the symlink-creation tests above.
- `ruff` and `mypy` are configured and clean on the current tree; mypy is
  best-effort type checking, not a proof of correctness.
- Tests are deterministic and offline (no network, no GPU); they do not exercise
  truly massive repositories, OS-level concurrent triggers, or network exposure,
  because those are outside Phase 1 scope.

## 12. Explicit non-goals for Phase 1

Intentionally **not** present, to avoid scope creep (and to keep the "no LLM, no
ML" constraint unambiguous):

- No LLMs, embeddings, or ML of any kind.
- No natural-language summaries or generated code.
- No multi-language support.
- No runtime instrumentation, no test execution, no dependency installation.
- No distributed/multi-tenant operation.
- No IDE/editor integration.
