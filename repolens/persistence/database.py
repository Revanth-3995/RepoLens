"""SQLite persistence layer (raw sqlite3, AD-5).

Schema, connection management, and simple data-access helpers. Transactions:
- PRAGMA foreign_keys=ON, journal_mode=WAL.
- Migration hook: schema_version table (current 2), with additive guards.
- The analysis service performs the atomic detail swap in a single
  transaction (deleting old run details, inserting the new ones, then marking
  the new run completed).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repolens.core.models import AnalysisSummary

SCHEMA_VERSION = 2
DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
  version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS repositories (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  root_path TEXT NOT NULL UNIQUE,
  registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_runs (
  id TEXT PRIMARY KEY,
  repository_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  summary_json TEXT,
  error TEXT,
  FOREIGN KEY(repository_id) REFERENCES repositories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_runs_repo_started ON analysis_runs(repository_id, started_at DESC);

CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY,
  repository_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  module_name TEXT NOT NULL,
  file_type TEXT NOT NULL DEFAULT 'python',
  size_bytes INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  parse_status TEXT NOT NULL CHECK(parse_status IN ('parsed','read_error','parse_error')),
  error_message TEXT,
  error_line INTEGER,
  error_offset INTEGER,
  UNIQUE(run_id, rel_path),
  FOREIGN KEY(repository_id) REFERENCES repositories(id) ON DELETE CASCADE,
  FOREIGN KEY(run_id) REFERENCES analysis_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_files_run ON files(run_id);
CREATE INDEX IF NOT EXISTS idx_files_repo_run ON files(repository_id, run_id);

CREATE TABLE IF NOT EXISTS symbols (
  id TEXT PRIMARY KEY,
  repository_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  file_id TEXT NOT NULL,
  name TEXT NOT NULL,
  qualified_name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('module','class','function','method')),
  parent_id TEXT,
  docstring TEXT,
  signature TEXT,
  parameters_json TEXT,
  return_annotation TEXT,
  bases_json TEXT,
  decorators_json TEXT,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  ordinal INTEGER NOT NULL DEFAULT 0,
  UNIQUE(run_id, file_id, qualified_name, kind, ordinal),
  FOREIGN KEY(repository_id) REFERENCES repositories(id) ON DELETE CASCADE,
  FOREIGN KEY(run_id) REFERENCES analysis_runs(id) ON DELETE CASCADE,
  FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE,
  FOREIGN KEY(parent_id) REFERENCES symbols(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_symbols_repo_run ON symbols(repository_id, run_id);
CREATE INDEX IF NOT EXISTS idx_symbols_run_name ON symbols(run_id, name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_run_kind ON symbols(run_id, kind);

CREATE TABLE IF NOT EXISTS relationships (
  id TEXT PRIMARY KEY,
  repository_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  source_file_id TEXT NOT NULL,
  source_symbol_id TEXT,
  rel_type TEXT NOT NULL CHECK(rel_type IN ('imports','calls','inherits_from')),
  source_line INTEGER NOT NULL,
  source_col INTEGER NOT NULL,
  target_symbol_id TEXT,
  target_ref TEXT NOT NULL,
  call_expr TEXT,
  resolution_status TEXT NOT NULL CHECK(resolution_status IN ('resolved_static','unresolved','ambiguous','external','dynamic_or_unknown')),
  evidence TEXT NOT NULL,
  UNIQUE(run_id, id),
  FOREIGN KEY(repository_id) REFERENCES repositories(id) ON DELETE CASCADE,
  FOREIGN KEY(run_id) REFERENCES analysis_runs(id) ON DELETE CASCADE,
  FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE CASCADE,
  FOREIGN KEY(source_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
  FOREIGN KEY(target_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_rels_run_type ON relationships(run_id, rel_type);
CREATE INDEX IF NOT EXISTS idx_rels_run_src_sym ON relationships(run_id, source_symbol_id);
CREATE INDEX IF NOT EXISTS idx_rels_run_tgt_sym ON relationships(run_id, target_symbol_id);

CREATE TABLE IF NOT EXISTS function_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repository_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  file_id TEXT NOT NULL,
  symbol_id TEXT NOT NULL DEFAULT '',
  qualified_name TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  parameter_count INTEGER NOT NULL DEFAULT 0,
  line_count INTEGER NOT NULL DEFAULT 0,
  statement_count INTEGER NOT NULL DEFAULT 0,
  if_count INTEGER NOT NULL DEFAULT 0,
  for_count INTEGER NOT NULL DEFAULT 0,
  while_count INTEGER NOT NULL DEFAULT 0,
  try_count INTEGER NOT NULL DEFAULT 0,
  with_count INTEGER NOT NULL DEFAULT 0,
  match_count INTEGER NOT NULL DEFAULT 0,
  return_count INTEGER NOT NULL DEFAULT 0,
  call_count INTEGER NOT NULL DEFAULT 0,
  max_nesting_depth INTEGER NOT NULL DEFAULT 0,
  cyclomatic_complexity INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY(repository_id) REFERENCES repositories(id) ON DELETE CASCADE,
  FOREIGN KEY(run_id) REFERENCES analysis_runs(id) ON DELETE CASCADE,
  FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fm_run ON function_metrics(run_id);
CREATE INDEX IF NOT EXISTS idx_fm_repo_run ON function_metrics(repository_id, run_id);

CREATE TABLE IF NOT EXISTS analysis_errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  repository_id TEXT NOT NULL,
  file_id TEXT,
  severity TEXT NOT NULL CHECK(severity IN ('error','warning')),
  category TEXT NOT NULL,
  message TEXT NOT NULL,
  rel_path TEXT,
  line INTEGER,
  offset INTEGER,
  FOREIGN KEY(run_id) REFERENCES analysis_runs(id) ON DELETE CASCADE,
  FOREIGN KEY(repository_id) REFERENCES repositories(id) ON DELETE CASCADE,
  FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_err_run ON analysis_errors(run_id);
"""


def _json(obj: Any) -> str:
    if obj is None:
        return "null"
    return json.dumps(obj, ensure_ascii=False)


def _load_json(s: str | None) -> Any:
    if not s or s == "null":
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def now() -> datetime:
    """Current UTC time as a naive datetime (stored as an ISO string)."""
    return datetime.now(UTC).replace(tzinfo=None)


class Database:
    """SQLite wrapper with one connection per thread and schema initialization.

    FastAPI runs synchronous path operations in a thread pool, so a single
    shared connection would be unsafe. Each thread lazily gets its own
    connection (WAL mode, busy timeout); :meth:`close` closes every connection
    this instance has opened.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.path),
                detect_types=sqlite3.PARSE_DECLTYPES,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._init_schema(conn)
            self._local.conn = conn
            with self._lock:
                self._all.append(conn)
        return conn

    def close(self) -> None:
        with self._lock:
            conns = list(self._all)
            self._all.clear()
        for c in conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(DDL)
        self._migrate(conn)
        cur = conn.execute("SELECT MAX(version) AS v FROM schema_meta")
        row = cur.fetchone()
        current = int(row["v"]) if row is not None and row["v"] is not None else None
        if current is None or current < SCHEMA_VERSION:
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,)
            )
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Idempotent, additive migrations for schemas created by older builds.

        ``CREATE TABLE IF NOT EXISTS`` never adds columns to an existing table.
        Each migration is guarded by an explicit column check so it is safe to
        run on every connection and never rewrites user data.
        """
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(function_metrics)").fetchall()
        }
        if "symbol_id" not in columns:
            conn.execute(
                "ALTER TABLE function_metrics ADD COLUMN symbol_id TEXT NOT NULL DEFAULT ''"
            )
        if "statement_count" not in columns:
            conn.execute(
                "ALTER TABLE function_metrics ADD COLUMN statement_count INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fm_symbol ON function_metrics(symbol_id)")
        conn.commit()

    @staticmethod
    def summary_to_json(s: AnalysisSummary | None) -> str:
        if s is None:
            return "null"
        return _json(s.model_dump())

    @staticmethod
    def json_to_summary(s: str | None) -> AnalysisSummary | None:
        data = _load_json(s)
        if not data:
            return None
        return AnalysisSummary.model_validate(data)
