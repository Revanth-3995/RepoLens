# RepoLens — Security Model (Phase 1)

Status: describes the security properties actually implemented in Phase 1.
Audience: operators and reviewers. Where a property is enforced by code, the
enforcing module is named; where a property is *not* provided, it is stated
plainly rather than implied.

## 1. What RepoLens is (and is not)

RepoLens Phase 1 is a **single-user, local, CPU-only backend tool**. It analyzes
source code as **untrusted input**. It is not a sandbox, not a multi-tenant
service, and not hardened for exposure to an untrusted network.

The security posture rests on three ideas:

1. **Nothing from the analyzed repository is ever executed, imported, installed,
   or modified.** Analysis is pure text/AST processing.
2. **All filesystem access is confined to the registered repository root**, even
   in the presence of `..`, absolute paths, and symlinks.
3. **Failures are contained and disclosed**, never hidden: a bad file yields a
   per-file error; a bad run leaves the previous good result intact; API errors
   never leak host paths or stack traces.

## 2. Trust boundaries and assets

| Asset | Where it lives | Trust |
|---|---|---|
| The RepoLens process and its SQLite database | operator-controlled host | trusted |
| The registered repository path | supplied by the operator at `POST /api/repositories` | trusted *as a root*; its contents are untrusted |
| Repository file contents (source, encodings, paths) | on disk under the root | **untrusted** |
| API responses | local HTTP client | trusted-client assumption (no auth, see §9) |

The repository root is the **trust root**. Once registered it is resolved once
(`Path.resolve()`), stored, and every subsequent access is checked against it.

## 3. No-execution guarantee

The analyzed code is never run. Concretely, the `repolens` package:

- does not import, `exec`, `eval`, or `compile` repository source;
- does not use `subprocess`, `runpy`, `os.system`/`os.popen`, `importlib`
  dynamic imports, `pickle`, or `ctypes`;
- does not invoke a shell, install packages, or run repository entry points,
  tests, or build scripts;
- does not follow imports found in the source (it only *records* them as facts).

This is enforced two ways:

1. **By design** — the pipeline (`analysis/pipeline.py`) reads bytes and hands
   them to `ast.parse`; the only consumer of parsed data is the symbol/import/
   call extractors, which produce plain records.
2. **By a meta-test** — `tests/unit/test_no_execution.py` parses the `repolens`
   package's own AST and fails if any dynamic-execution primitive, dangerous
   module import (`subprocess`, `runpy`, `pickle`, `importlib`, `ctypes`), or
   import of the `tests` fixtures appears. This runs in CI/local gates as a
   regression guard: a future change that reintroduces execution fails the suite.

Residual risk: Python's `ast` parser is implemented in C. A hypothetical memory
safety bug in the interpreter triggered by pathological input is outside
RepoLens's control and is not mitigated here beyond the size/count limits in §6.

## 4. Path traversal and root containment

Multiple independent layers reject paths that try to leave the root. All are
exercised by tests (`tests/unit/test_path_safety.py`,
`tests/integration/test_security_api.py`).

**Discovery (`ingestion/discovery.py`)**

- The root is re-resolved defensively at the start of discovery.
- `is_within_root(root, candidate)` resolves both paths and requires
  `relative_to(root)` to succeed; `..`, symlinked parents, and drive changes
  therefore cannot escape.
- Directory links — symlinks **and Windows directory junctions** — are never
  traversed (recorded as `symlinked_directory`). `Path.is_symlink()` reports
  `False` for junctions, so `is_link_like()` additionally checks
  `Path.is_junction()` and the reparse-point attribute as a fallback.
- A file link is read only if its **resolved real path** stays inside the
  root; otherwise it is rejected as `symlink_escapes_root`.

**Source retrieval (`services/analysis_service.py`, `get_file_detail`)**

- `validate_rel_path()` rejects empty paths, `~`-prefixed and `/`-prefixed
  (absolute) paths, drive-qualified paths (`C:`), and any `..` segment. It also
  normalizes backslashes and redundant `./`/`//` to a canonical POSIX path.
- The requested path must additionally exist as a `files` row in the database
  for the repository's latest completed run — a path that was never part of the
  analysis is a `404`, not a read.
- Before reading, `is_within_root(root, root / normalized)` is checked **again**;
  only then is the file read (`read_source_file`).

**URL handling** — the route uses `{rel_path:path}`, so literal `..` segments
are collapsed by the HTTP layer before reaching the app; the service validation
above is the defense that does not depend on that behavior. Tests send literal
and percent-encoded traversal (`../secret.py`, `..%2Fsecret.py`,
`%2e%2e%2fsecret.py`, `sub/../../secret.py`) and assert no outside content is
served.

**Confidentiality of the root path** — absolute host paths are returned only in
the operator-initiated registration response. File, symbol, relationship, and
graph responses use repository-relative POSIX paths only. Error responses never
include absolute paths (asserted by `test_error_responses_do_not_leak_absolute_paths`).

## 5. Symlink policy (decision AD-13)

- **Directory symlinks are never traversed.** Prevents infinite loops and
  escapes via linked directory trees.
- **Windows directory junctions are treated exactly like directory
  symlinks.** An NTFS junction is a reparse point that requires *no* privilege
  to create (`mklink /J`) while `Path.is_symlink()` returns `False` for it;
  discovery therefore classifies junctions as `symlinked_directory` too, so a
  junction pointing outside the root cannot smuggle outside files into an
  analysis.
- **File links are allowed only when the resolved real target is inside the
  root**; otherwise rejected (`symlink_escapes_root`). Rejections are recorded
  in discovery results, not silently dropped.
- Containment uses `Path.resolve()` / `os.path.realpath()`, so a chain of links
  is evaluated against its final target.

Environmental caveat: on Windows, creating symlinks may require Administrator
privileges or Developer Mode. The two symlink tests (`test_directory_symlink_not_traversed`,
`test_symlink_file_escaping_root_excluded`) attempt creation and `skip` explicitly
if the OS refuses. Because a **junction needs no privilege**, discovery is also
covered by a junction-based test that never skips on Windows, so the "never
traverse a directory link" branch is exercised on this host even though the
symlink-creation tests skip. Findings are disclosed in
`docs/phase-1-final-audit.md`.

## 6. Denial-of-service / resource limits

Analysis is synchronous and bounded so a hostile or merely huge repository
cannot exhaust the process:

| Limit | Setting | Default | Enforced in |
|---|---|---|---|
| Max bytes per file | `REPOLENS_MAX_FILE_BYTES` | 1 000 000 | discovery + reader (defense in depth) |
| Max files analyzed | `REPOLENS_MAX_FILES` | 5 000 | discovery (`truncated=true` recorded) |
| Path-search depth | `REPOLENS_PATH_MAX_DEPTH` | 6 (hard cap 10) | `GraphQuery.path` |
| Path-search visited nodes | `REPOLENS_PATH_MAX_NODES` | 20 000 | `GraphQuery.path` |
| Page size | — | default 50, max 500 | every list endpoint |

- Oversized files are rejected per-file (`file_too_large`), never fatal.
- Hitting `max_files` stops discovery globally and records a `limit_exceeded`
  warning; it does not silently over-read.
- Directory exclusions (`ingestion/filters.py`) prune `.git`, virtualenvs,
  caches, `node_modules`, `site-packages`, build output, etc., reducing the
  attack surface and work performed.
- Graph path search is iterative BFS with both a depth cap and a visited-node
  cap; when a bound is hit it returns `found=false` with a reason — it never
  fabricates a path.

Residual risk: because analysis is synchronous, a very large *legal* repository
(up to the limits) will occupy a request for its duration. There is no job
queue or timeout in Phase 1 (documented in `docs/limitations.md`).

## 7. Input parsing and encoding safety

- Each file is read **as bytes**, hashed (SHA-256), then decoded: BOM sniff for
  UTF-8 / UTF-16 LE / UTF-16 BE, otherwise strict `utf-8-sig`.
- Decode failures and read failures become **per-file** `read_error` rows with a
  safe message (`invalid_encoding: <reason>`), never a repository-wide crash.
- Parsing uses `ast.parse`; `SyntaxError` becomes a per-file `parse_error` with
  line/offset, and analysis continues with the remaining files.
- Source **text is not stored** in SQLite (decision AD-8). Only the content
  hash, size, and parse metadata are persisted, limiting data-at-rest exposure.
- On retrieval, the stored hash is compared to the on-disk hash; a mismatch is
  reported as `content_changed_since_analysis: true` rather than silently
  serving mismatched content.

## 8. Persistence and injection safety

- All SQL uses **parameterized statements** (`?` placeholders) through
  `sqlite3`; no string interpolation of user- or repository-derived values goes
  into SQL. Dynamic fragments are limited to fixed, code-controlled clause
  strings (e.g., `kind = ?`).
- The database is a single file (`REPOLENS_DB_PATH`, default `repolens.db`),
  opened with `PRAGMA foreign_keys=ON` and WAL journaling.
- Connections are **thread-local** (one per thread) with `busy_timeout=5000`,
  avoiding cross-thread connection sharing.
- Re-analysis is transactional (decision AD-2): details of older runs are
  deleted and the new run's details inserted in a single transaction, then the
  run is marked `completed`. A failure rolls back, leaving the previous
  completed result intact; the failed run is recorded separately.
- Foreign keys and `CHECK` constraints (statuses, kinds, relation types)
  constrain the stored domain and reject malformed rows at the database layer.

## 9. API error handling and information disclosure

- Domain errors are mapped to HTTP status codes by `_STATUS_BY_ERROR` in
  `repolens/main.py` and returned in a stable envelope:
  `{"error": {"code": "...", "detail": "..."}}`.
- The catch-all handler logs the full traceback **server-side** and returns a
  generic `internal_error` / `internal server error` to the client — no stack
  traces, no exception internals.
- File/symbol/relationship/graph payloads contain relative paths only; the
  absolute root is not echoed.
- OpenAPI (`/docs`, `/openapi.json`) is exposed by FastAPI for developer use.

## 10. Authentication and authorization — explicitly out of scope

**Phase 1 has no authentication, no authorization, no TLS, and no rate
limiting.** It binds to `127.0.0.1` by default (`REPOLENS_HOST`) and is intended
to run on a single operator's machine.

Implications:

- Anyone who can reach the port can register arbitrary directories that the
  process can read, trigger analysis, and read source through
  `?include_source=true`.
- The tool therefore effectively reads and serves any Python file readable by
  the process **if the operator registers its directory**. This is intended
  behavior for a local developer tool, not a vulnerability, but it means
  RepoLens **must not be exposed to an untrusted network** without adding an
  auth/reverse-proxy layer (out of scope for Phase 1).

## 11. Summary of guarantees and non-guarantees

**Guaranteed (implemented and tested):**

- Analyzed code is never executed, imported, or modified (§3).
- Filesystem reads are confined to the registered root, including under `..`,
  absolute-path, drive-qualified, and escaping-symlink attempts (§4, §5).
- A single bad file cannot abort a run; errors are per-file and retrievable (§7).
- A failed re-analysis cannot corrupt the last completed result (§8).
- Resource use is bounded by configurable size/count/path limits (§6).
- API errors do not leak absolute paths or tracebacks (§9).

**Not provided (by design in Phase 1):**

- Authentication, authorization, multi-tenancy, TLS, rate limiting (§10).
- Sandboxing of the RepoLens process itself.
- Protection against interpreter-level (C) parser vulnerabilities.
- Async/queued isolation of long-running analyses (§6 residual risk).
