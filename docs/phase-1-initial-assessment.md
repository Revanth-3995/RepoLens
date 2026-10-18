# RepoLens — Phase 1 Initial Assessment (Stage 0)

Date: 2026-10-18
Author: Implementation agent (Stage 0 inspection)

## 1. Workspace state

- Working directory / repository root: `C:\Users\REVANTH VISHNU REDDY\Desktop\Repolens`
- Git repository: yes. Branch `main`, **no commits yet** (`git status`: "No commits yet").
- The directory contains **only** the `.git` metadata directory. There are no tracked or
  untracked files, no hidden configuration files, no README, no `.gitignore`, no dependency
  manifests, and no environment configuration.
- **Conclusion: the repository is genuinely empty. There is no pre-existing implementation and
  no meaningful application/test baseline. No baseline is claimed or invented.**
- Git identity is configured locally/global for committing (`user.name`/`user.email` present).

## 2. Existing files and configuration

None. Everything under `repolens/`, `tests/`, `docs/`, plus `pyproject.toml`, `.gitignore`,
and `README.md`, will be created from scratch.

## 3. Available runtime and development tools

Verified with `python --version`, `python -m pip list`:

| Tool | Version | Notes |
|---|---|---|
| Python | 3.13.3 | `C:\Users\...\Python\Python313\python.exe` (64-bit Windows) |
| pip | 25.3 | |
| pytest | 9.0.1 | test runner |
| pytest-asyncio | 1.3.0 | present; Phase 1 tests are synchronous, may not be needed |
| httpx | 0.27.2 | needed for FastAPI `TestClient` |
| FastAPI | 0.121.3 | HTTP API framework |
| starlette | 0.50.0 | transitive of FastAPI |
| pydantic | 2.13.4 | request/response and domain models |
| pydantic-settings | 2.13.1 | available; see architecture decision AD-11 (not used — see docs) |
| NetworkX | 3.1 | in-memory graph |
| SQLAlchemy | 2.0.43 | available; see architecture decision AD-5 (not used) |
| uvicorn | 0.38.0 | ASGI server to run the API |
| Ruff | 0.15.12 | linting + formatting |
| mypy | 2.0.0 | optional static type checking |

Python's standard library provides `ast` (parsing), `sqlite3` (persistence), `hashlib`,
`os`, `pathlib`, `tokenize`-adjacent helpers — all sufficient for Phase 1 without adding
dependencies.

Network access is not required for development or testing (all dependencies are already
installed). No GPU is required. The host is Windows, which matters for symlink tests
(see risks).

## 4. Initial implementation assumptions

1. Phase 1 is a single-user, local, CPU-only backend tool; no authentication/authorization
   layer is required (documented in `docs/security.md`).
2. Analysis is synchronous and bounded by configurable file-count/size limits. There is no
   job queue; the API blocks until the run finishes and this is documented honestly.
3. Python is the only analyzed language in Phase 1. Non-`.py` files are ignored.
4. The analyzed repository is treated as **untrusted input**: never executed, never imported,
   never modified.
5. Public API responses expose repository-relative POSIX paths only, never absolute host paths
   (except at registration time, where the operator supplies the path).
6. One SQLite database file serves all registered repositories; its location is configurable
   via environment variable.

## 5. Architecture decisions that need to be made (and were made)

These are recorded here as *pending decisions* at Stage 0; the resolved decisions and
rationale live in `docs/architecture.md` (AD-1 .. AD-14).

1. Identifier strategy for repositories, files, symbols, and relationships.
2. Re-analysis model (versioned runs vs. destructive replacement) and crash safety.
3. Representation of unresolved/external/ambiguous relationships (records without graph edges).
4. Module vs. file node unification in the graph.
5. Raw `sqlite3` vs. SQLAlchemy for persistence.
6. Conservative call-resolution rules (what may be marked `resolved_static`).
7. Symbol nesting/identity under file edits (honest stability guarantees).
8. Source text storage (DB vs. on-demand read from disk with boundary validation).
9. Error taxonomy (parse failures, read failures, discovery warnings).
10. Pagination/limit defaults for all list endpoints.
11. Configuration mechanism (env-var backed frozen dataclass vs. pydantic-settings).
12. Encoding policy for reading source files.
13. Symlink policy (never traverse directory symlinks; file symlinks only when target stays
    inside the registered root).
14. Path-search bounds for graph path queries.

## 6. Identified risks and environmental limitations

1. **Windows symlinks**: creating symlinks (`os.symlink`) may require Administrator rights or
   Developer Mode. Symlink-escape tests must attempt creation and, if the OS refuses,
   report the skip explicitly rather than silently. This is an environmental limitation that
   will be disclosed in the final audit.
2. **Windows path semantics**: case-insensitive file systems, backslashes, and drive letters.
   All persisted paths are normalized to POSIX-style relative paths; containment checks use
   `Path.resolve()`.
3. **Encoding**: some fixture files are intentionally invalid UTF-8; reading must fail
   gracefully per-file, not per-repository.
4. **Scale**: NetworkX graph and symbol indexes are rebuilt from SQLite per graph request.
   Acceptable for Phase 1 scale (bounded by limits); documented as a limitation.
5. **Conservative resolution**: dynamic dispatch (`obj.method()`), monkey-patching,
   `getattr`-based calls, and star imports cannot be resolved statically; the system must
   report them as unresolved/ambiguous/dynamic rather than guessing. This limits call-graph
   completeness by design and will be documented in `docs/limitations.md`.
6. **Python 3.13 AST**: files using syntax newer than the running interpreter will fail to
   parse; they are recorded per-file, not fatal.

## 7. Planned implementation order

1. Stage 0 inspection (this document).
2. `docs/architecture.md` with core data model, API contracts, testing strategy.
3. Project foundation: `pyproject.toml`, `.gitignore`, package skeleton, configuration,
   core errors/identifiers/models.
4. Persistence layer: SQLite schema, connection handling, data-access functions.
5. Ingestion: discovery, filters, safe reading.
6. Analysis: AST parsing, symbol extraction, import/call extraction, conservative resolver.
7. Graph: builder + integrity validation + queries.
8. Services: repository registration, analysis orchestration (transaction-safe persistence,
   re-analysis behavior).
9. API: FastAPI app, routers, error handling, OpenAPI.
10. Fixtures A–D under `tests/fixtures/repositories/`.
11. Unit + integration test suite.
12. Documentation (`README.md`, `docs/api.md`, `docs/security.md`, `docs/limitations.md`).
13. Ordinary verification (pytest, ruff, mypy) then the **independent audit**
    (`docs/phase-1-final-audit.md`), fixes, re-runs, final verdict.

## 8. Traceability checklist (requirement → planned component → planned tests)

| # | Phase 1 requirement | Component | Tests |
|---|---|---|---|
| T1 | Workspace inspection recorded | `docs/phase-1-initial-assessment.md` | — |
| T2 | Architecture documented | `docs/architecture.md` | — |
| T3 | Register repository via API (path validation, stable ID) | `services/repository_service.py`, `api/routes/repositories.py`, `core/identifiers.py` | `tests/unit/test_repository_service.py`, `tests/integration/test_api_repository.py` |
| T4 | Recursive deterministic discovery + exclusions | `ingestion/discovery.py`, `ingestion/filters.py` | `tests/unit/test_discovery.py` |
| T5 | File reading, size limits, encodings, hash | `ingestion/reader.py`, `config.py` | `tests/unit/test_reader.py` |
| T6 | Path-traversal + escaping-symlink blocking | `ingestion/discovery.py`, `services/analysis_service.py` (source endpoint) | `tests/unit/test_path_safety.py`, `tests/integration/test_security_api.py` |
| T7 | AST parsing; per-file failures isolated | `analysis/python_parser.py`, `analysis/pipeline.py` | `tests/unit/test_parser.py`, fixture D integration |
| T8 | Symbol extraction (classes, functions, methods, nested, signatures, decorators, docstrings, lines) | `analysis/symbols.py` | `tests/unit/test_symbols.py`, fixture B integration |
| T9 | Symbol identity collision avoidance + documented stability | `core/identifiers.py`, `analysis/symbols.py` | `tests/unit/test_identifiers.py`, `tests/unit/test_symbols.py` |
| T10 | Import extraction (all forms, relative, aliased) | `analysis/imports_calls.py` | `tests/unit/test_imports_calls.py`, fixture C |
| T11 | Call extraction (name/attr/self/indirect/nested) | `analysis/imports_calls.py` | `tests/unit/test_imports_calls.py`, fixtures A/C |
| T12 | Conservative resolution + explicit statuses; unresolved preserved | `analysis/resolver.py` | `tests/unit/test_resolver.py`, fixture C |
| T13 | Graph nodes/edges consistent with facts; integrity; dedupe | `graph/builder.py` | `tests/unit/test_graph_builder.py` |
| T14 | Graph queries: neighbors, type filter, bounded path | `graph/queries.py`, `api/routes/graph.py` | `tests/unit/test_graph_queries.py`, `tests/integration/test_api_graph.py` |
| T15 | Persistence: schema, FKs, unique constraints, indexes | `persistence/database.py` | `tests/unit/test_database.py` |
| T16 | Re-analysis: no duplicates, stale-data control, failed run safety | `services/analysis_service.py`, `persistence/repositories.py` | `tests/unit/test_reanalysis.py`, integration re-run tests |
| T17 | Analysis summary counts from real results | `analysis/pipeline.py` | unit + integration summary assertions |
| T18 | Errors/warnings retrievable per run | `api/routes/analysis.py` | `tests/integration/test_api_analysis.py` |
| T19 | Files/symbols endpoints with filters + pagination | `api/routes/files.py`, `api/routes/symbols.py` | `tests/integration/test_api_files_symbols.py` |
| T20 | Source retrieval with strict root-boundary validation | `services/analysis_service.py` | `tests/integration/test_security_api.py` |
| T21 | Analyzed code never executed | whole pipeline (design), meta-test scanning package source | `tests/unit/test_no_execution.py` |
| T22 | Fixture repositories A–D | `tests/fixtures/repositories/*` | consumed by integration tests |
| T23 | Documentation matches implementation | `README.md`, `docs/*` | audit verification |
| T24 | Independent final audit with evidence | `docs/phase-1-final-audit.md` | audit stage |

Status at Stage 0: **Phase 1 is not implemented yet.** This document only records the
inspection and the plan.
