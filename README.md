# RepoLens — Deterministic Code Comprehension for Python

RepoLens is a **no-LLM static-analysis system** for local Python repositories. It
ingests a repository, parses it with Python's `ast`, extracts symbols, imports,
calls and inheritance, resolves them conservatively, builds a code graph with
NetworkX, and adds deterministic code-comprehension layers: structural metrics,
control-flow graphs, local data flow, evidence-backed explanations, lexical
search, static tracing, and architecture indicators. The analyzed Phase 1 facts
and Phase 2 function metrics are persisted in SQLite behind a FastAPI HTTP API;
the comprehension layers are derived on demand from those facts.

There are **no LLMs, generative APIs, hosted AI, embeddings, model training, or
cosmetic ML**. Every fact is derived from source text and reported with explicit
provenance or an honest approximation label. Analyzed code is **never executed,
imported, or modified**.

---

## What RepoLens does

### Foundation

1. **Safe ingestion** — recursive discovery of `.py` files with configurable
   directory exclusions, size and count limits, and symlink defenses.
2. **Parsing** — each file is parsed independently; a syntax error in one file
   never aborts the run.
3. **Symbol extraction** — modules, classes, functions and methods, including
   nested scopes, signatures, parameters, annotations, decorators, base
   classes, docstrings and line ranges.
4. **Import & call extraction** — every import form (plain, aliased, relative,
   star) and calls (name, attribute, self/cls, indirect/`getattr`).
5. **Conservative resolution** — each extracted relationship is given an
   explicit status: `resolved_static`, `unresolved`, `ambiguous`, `external`,
   or `dynamic_or_unknown`. Nothing uncertain is guessed.
6. **Code graph** — a directed NetworkX graph; only `resolved_static`
   relationships become edges, unresolved references are retained as database
   records.
7. **Persistence** — raw `sqlite3` with versioned runs and an atomic
   detail-swap so a failed re-analysis can never corrupt the last good result.

### Phase 2 comprehension

8. **Function metrics** — parameters, lines, statements, nesting, calls, returns
   and an AST decision-point complexity estimate, persisted per function and
   queryable by symbol.
9. **Control-flow graphs** — entry/exit, if/elif/else merges, loops and back
   edges, break/continue, return/raise, try/try*/with, match/case, nesting,
   source locations, and explicit truncation notes.
10. **Local data flow** — bounded intraprocedural may reaching definitions,
    def-use links, read-before-def reporting, parameters, reassignment kills,
    loop/branch joins, walrus/pattern targets, and explicit scope limits.
11. **Evidence-backed explanations** — statement, function, file, folder and
    project levels with separate `facts`, `heuristics`, `unknowns`, `evidence`
    and source excerpts. Templates are deterministic; intent is never invented.
12. **Lexical search** — symbol, identifier, text and token modes with kind/path
    filters, snippets, enclosing-symbol links, stable integer ranking and
    pagination. No embeddings.
13. **Static tracing** — forward, reverse and bidirectional call/import/
    inheritance traversal, bounded paths, cross-file evidence, cycle reporting,
    and explicit unresolved/external/ambiguous references. A trace is a static
    relationship path, not a runtime execution path.
14. **Architecture indicators** — resolved-static import graph, canonical
    cycles, degree centrality, fan-in/fan-out, instability, complexity hotspots,
    entry points, and evidence-linked findings. Metrics never label code
    defective on their own.

See [`docs/phase-2-plan.md`](docs/phase-2-plan.md) for the authoritative Phase 2
matrix, formulas, semantics, bounds, and limitations.

## Requirements

- Python **3.11+** (developed and verified on CPython 3.13)
- Runtime dependencies: `fastapi`, `uvicorn`, `pydantic`, `networkx`
- Dev/test: `pytest`, `httpx`, `ruff`, `mypy`

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run the API

```bash
python -m repolens.main
# or
uvicorn repolens.main:app --host 127.0.0.1 --port 8000
```

Interactive docs: <http://127.0.0.1:8000/docs> · OpenAPI: `/openapi.json` ·
Health: `/health`.

Configuration is environment-based (prefix `REPOLENS_`): `DB_PATH`,
`MAX_FILE_BYTES`, `MAX_FILES`, `PATH_MAX_DEPTH`, `PATH_MAX_NODES`,
`TRACE_MAX_DEPTH`, `TRACE_MAX_NODES`, `SEARCH_MAX_FILES`, `SEARCH_MAX_HITS`,
`COMPLEXITY_WARNING_THRESHOLD`, `FAN_OUT_WARNING_THRESHOLD`,
`FAN_IN_WARNING_THRESHOLD`, `LOG_LEVEL`, `HOST`, `PORT`. Other Phase 2 resource
bounds are `Settings` fields with documented defaults in
[`docs/phase-2-plan.md`](docs/phase-2-plan.md).

## Quick start

```bash
# 1. Register a repository by absolute path
curl -X POST http://127.0.0.1:8000/api/repositories \
  -H "Content-Type: application/json" \
  -d '{"name": "my-project", "path": "/abs/path/to/my-project"}'

# 2. Analyze it (synchronous)
curl -X POST http://127.0.0.1:8000/api/repositories/<repo_id>/analysis

# 3. Explore structural facts
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/files"
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/symbols?kind=class"
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/graph/stats"

# 4. Understand functions
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/metrics/functions"
curl "http://127.0.0.1:8000/api/symbols/<symbol_id>/cfg"
curl "http://127.0.0.1:8000/api/symbols/<symbol_id>/dataflow"

# 5. Explain, search, trace, review architecture
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/explanations/project"
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/search?q=main&mode=all"
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/trace?symbol_id=<symbol_id>&direction=out"
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/architecture"
```

The full surface, parameters, response fields, formulas and error envelope are
in [`docs/api.md`](docs/api.md).

## Quality gates

```bash
python -m pytest tests -o addopts="" -p no:cacheprovider
python -m ruff check .
python -m ruff format --check .
python -m mypy repolens
git diff --check
```

Current verification on the development host: **252 tests passed, 2 skipped**.
The two skips are Windows symlink-creation tests (`WinError 1314`) and are not
counted as passing.

The suite includes unit tests for every Phase 2 engine plus integration and
end-to-end fixtures with multi-file imports/calls, nested functions, branches,
loops, exceptions, dependency cycles, empty files, and parse-error files. The
E2E fixture proves analyzed code is not executed.

## Documentation

| Document | Contents |
|---|---|
| [`docs/phase-2-plan.md`](docs/phase-2-plan.md) | Authoritative Phase 2 matrix, formulas, semantics, limits and repro steps |
| [`docs/api.md`](docs/api.md) | HTTP API reference, including Phase 2 endpoints |
| [`docs/architecture.md`](docs/architecture.md) | Pipeline, data model, Phase 2 modules, decisions |
| [`docs/security.md`](docs/security.md) | Threat model and safety properties |
| [`docs/limitations.md`](docs/limitations.md) | Honest scope and known limitations |
| [`docs/phase-1-final-audit.md`](docs/phase-1-final-audit.md) | Phase 1 independent audit and verdict |

## Scope

RepoLens analyzes **Python only**. It is a single-user, local, CPU-only tool with
no authentication. It is intentionally conservative: when a relationship cannot
be determined statically, it is reported as such rather than guessed. It does
not perform interprocedural data flow, alias/points-to analysis, runtime
tracing, semantic/vector search, or automatic defect verdicts. See
[`docs/limitations.md`](docs/limitations.md) before relying on results.
