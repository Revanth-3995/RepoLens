# RepoLens — Phase 1

RepoLens is a **no-LLM static-analysis system** for local Python repositories.
It ingests a repository, parses it with Python's `ast`, extracts symbols,
imports, calls and inheritance, resolves them conservatively, builds a code
graph with NetworkX, and persists everything to SQLite behind a FastAPI HTTP
API.

Phase 1 deliberately contains **no machine learning and no language model**.
Every fact is derived from source text and reported with explicit provenance.

---

## What Phase 1 does

1. **Safe ingestion** — recursive discovery of `.py` files with configurable
   directory exclusions, size and count limits, and symlink defenses. Analyzed
   code is **never executed, imported, or modified**.
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
8. **HTTP API** — a documented FastAPI surface for repositories, analysis runs,
   files, symbols, relationships and graph queries.

See [`docs/architecture.md`](docs/architecture.md) for the full design and the
binding decisions (AD-1 … AD-14).

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
`MAX_FILE_BYTES`, `MAX_FILES`, `PATH_MAX_DEPTH`, `PATH_MAX_NODES`, `LOG_LEVEL`,
`HOST`, `PORT`.

## Quick start

```bash
# 1. Register a repository by absolute path
curl -X POST http://127.0.0.1:8000/api/repositories \
  -H "Content-Type: application/json" \
  -d '{"name": "my-project", "path": "/abs/path/to/my-project"}'

# 2. Analyze it (synchronous)
curl -X POST http://127.0.0.1:8000/api/repositories/<repo_id>/analysis

# 3. Explore
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/files"
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/symbols?kind=class"
curl "http://127.0.0.1:8000/api/repositories/<repo_id>/graph/stats"
```

The full surface, with examples and the error envelope, is in
[`docs/api.md`](docs/api.md).

## Quality gates

```bash
python -m pytest            # 139 tests, 2 skipped (unit + integration)
python -m ruff check .      # lint
python -m mypy repolens     # type check
```

The test suite includes fixture repositories A–D under
`tests/fixtures/repositories/`.

## Documentation

| Document | Contents |
|---|---|
| [`docs/phase-1-initial-assessment.md`](docs/phase-1-initial-assessment.md) | Stage 0 workspace inspection and plan |
| [`docs/architecture.md`](docs/architecture.md) | Data model, pipeline, decisions AD-1…AD-14 |
| [`docs/api.md`](docs/api.md) | HTTP API reference |
| [`docs/security.md`](docs/security.md) | Threat model and safety properties |
| [`docs/limitations.md`](docs/limitations.md) | Honest scope and known limitations |
| [`docs/phase-1-final-audit.md`](docs/phase-1-final-audit.md) | Independent audit and verdict |

## Scope

Phase 1 analyzes **Python only**. It is a single-user, local, CPU-only tool with
no authentication. It is intentionally conservative: when a relationship cannot
be determined statically, it is reported as such rather than guessed. See
[`docs/limitations.md`](docs/limitations.md) before relying on results.
