"""Analysis orchestration: run the pipeline and persist results atomically.

Atomic re-analysis behavior (AD-2 in docs/architecture.md):
1. A ``running`` run row is created first.
2. The pipeline computes the complete fact set in memory.
3. One transaction deletes the detail rows (files/symbols/relationships) of
   all older runs for the repository, inserts the new run's detail rows, and
   flips the new run to ``completed`` — commit is the atomic switch.
4. Any failure before commit rolls the transaction back: previously completed
   data stays untouched, and the failed run is marked ``failed`` with its
   error in a separate transaction.

Only the latest completed run's details are queryable; run history and issues
are preserved for diagnosis.
"""

from __future__ import annotations

from repolens.analysis.pipeline import AnalysisPipeline
from repolens.config import Settings
from repolens.core.errors import (
    AnalysisRunNotFoundError,
    FileNotFoundError_,
    PathTraversalError,
    SymbolNotFoundError,
)
from repolens.core.identifiers import new_run_id
from repolens.core.models import (
    AnalysisIssue,
    AnalysisRun,
    AnalysisSummary,
    FileDetail,
    FileRecord,
    IssueSeverity,
    PageMeta,
    PaginatedFiles,
    PaginatedIssues,
    PaginatedRelationships,
    PaginatedSymbols,
    ParameterModel,
    ParseStatus,
    RelationshipRecord,
    RelationshipType,
    ResolutionStatus,
    RunStatus,
    SymbolKind,
    SymbolRecord,
)
from repolens.ingestion.discovery import is_within_root
from repolens.ingestion.reader import read_source_file
from repolens.persistence.database import Database, _json, _load_json, iso, now
from repolens.persistence.repositories import (
    _row_to_file,
    _row_to_rel,
    _row_to_run,
    _row_to_symbol,
)
from repolens.services.repository_service import RepositoryService

_MAX_PAGE = 500


def _build_summary(outcome) -> AnalysisSummary:
    """Derive every count from the actual analysis outcome."""
    parsed = sum(1 for f in outcome.files if f.parse_status == ParseStatus.PARSED)
    read_err = sum(1 for f in outcome.files if f.parse_status == ParseStatus.READ_ERROR)
    parse_err = sum(1 for f in outcome.files if f.parse_status == ParseStatus.PARSE_ERROR)
    symbols = sum(len(f.symbols) for f in outcome.files)
    rels = len(outcome.relationships)

    def count(status: ResolutionStatus) -> int:
        return sum(1 for r in outcome.relationships if r.resolution_status is status)

    errors = sum(1 for i in outcome.issues if i.severity is IssueSeverity.ERROR)
    warnings = sum(1 for i in outcome.issues if i.severity is IssueSeverity.WARNING)
    return AnalysisSummary(
        files_discovered=outcome.discovered_count,
        files_excluded=outcome.excluded_files,
        excluded_directories=outcome.excluded_directories,
        files_read=len(outcome.files) - read_err,
        files_parsed=parsed,
        files_read_errors=read_err,
        files_parse_errors=parse_err,
        symbols_extracted=symbols,
        relationships_extracted=rels,
        relationships_resolved=count(ResolutionStatus.RESOLVED_STATIC),
        relationships_unresolved=count(ResolutionStatus.UNRESOLVED),
        relationships_external=count(ResolutionStatus.EXTERNAL),
        relationships_ambiguous=count(ResolutionStatus.AMBIGUOUS),
        relationships_dynamic=count(ResolutionStatus.DYNAMIC_OR_UNKNOWN),
        warnings=warnings,
        errors=errors,
        duration_seconds=round(outcome.duration_seconds, 6),
        truncated=outcome.truncated,
    )


def validate_rel_path(rel_path: str) -> str:
    """Reject traversal or absolute-looking relative paths; return normalized POSIX path.

    Raises :class:`PathTraversalError` on any violation.
    """
    cleaned = rel_path.replace("\\", "/").strip()
    if not cleaned:
        raise PathTraversalError("empty path")
    if cleaned.startswith("/") or cleaned.startswith("~"):
        raise PathTraversalError("absolute paths are not allowed")
    if ":" in cleaned.split("/")[0]:
        raise PathTraversalError("drive-qualified paths are not allowed")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts:
        raise PathTraversalError("empty path")
    if any(p == ".." for p in parts):
        raise PathTraversalError("path traversal ('..') is not allowed")
    return "/".join(parts)


class AnalysisService:
    """Synchronous analysis trigger + query access to persisted facts."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.repo_svc = RepositoryService(db, settings)

    # ------------------------------------------------------------------ run

    def trigger(self, rid: str) -> AnalysisRun:
        """Run analysis synchronously and persist atomically."""
        root = self.repo_svc.get_root(rid)
        run_id = new_run_id()
        started = now()
        conn = self.db.connect()
        conn.execute(
            "INSERT INTO analysis_runs (id, repository_id, status, started_at) VALUES (?, ?, ?, ?)",
            (run_id, rid, RunStatus.RUNNING.value, iso(started)),
        )
        conn.commit()

        pipeline = AnalysisPipeline(self.settings)
        try:
            outcome = pipeline.analyze(rid, root)
            summary = _build_summary(outcome)
        except Exception as exc:  # noqa: BLE001 - record any pipeline failure on the run
            self._mark_failed(run_id, str(exc))
            raise

        finished = now()
        conn = self.db.connect()
        try:
            with self._transaction(conn):
                # Remove details of superseded runs (cascades symbols/relationships
                # are explicit deletes to stay independent of cascade timing).
                conn.execute(
                    "DELETE FROM relationships WHERE repository_id = ? AND run_id != ?",
                    (rid, run_id),
                )
                conn.execute(
                    "DELETE FROM symbols WHERE repository_id = ? AND run_id != ?",
                    (rid, run_id),
                )
                conn.execute(
                    "DELETE FROM files WHERE repository_id = ? AND run_id != ?",
                    (rid, run_id),
                )
                conn.execute("DELETE FROM analysis_errors WHERE run_id = ?", (run_id,))

                file_ids = set()
                for f in outcome.files:
                    if not f.file_id:
                        continue
                    conn.execute(
                        "INSERT INTO files (id, repository_id, run_id, rel_path, module_name,"
                        " file_type, size_bytes, content_hash, parse_status, error_message,"
                        " error_line, error_offset) VALUES (?,?,?,?,?,'python',?,?,?,?,?,?)",
                        (
                            f.file_id,
                            rid,
                            run_id,
                            f.rel_path,
                            f.module_name,
                            f.size_bytes,
                            f.content_hash,
                            f.parse_status.value,
                            f.error_message,
                            f.error_line,
                            f.error_offset,
                        ),
                    )
                    file_ids.add(f.file_id)
                    for s in f.symbols:
                        parent_id = s.parent_id if s.parent_id else None
                        conn.execute(
                            "INSERT INTO symbols (id, repository_id, run_id, file_id, name,"
                            " qualified_name, kind, parent_id, docstring, signature,"
                            " parameters_json, return_annotation, bases_json, decorators_json,"
                            " start_line, end_line, ordinal) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                s.symbol_id,
                                rid,
                                run_id,
                                s.file_id,
                                s.name,
                                s.qualified_name,
                                s.kind.value,
                                parent_id,
                                s.docstring,
                                s.signature,
                                _json([vars(p) for p in s.parameters]),
                                s.return_annotation,
                                _json(s.bases),
                                _json(s.decorators),
                                s.start_line,
                                s.end_line,
                                s.ordinal,
                            ),
                        )

                for r in outcome.relationships:
                    if r.source_file_id not in file_ids:
                        continue
                    if r.target_symbol_id and not self._symbol_exists(conn, r.target_symbol_id):
                        # target symbol belongs to a file that failed to parse: keep
                        # the relationship but drop the dangling id (honest record).
                        r.target_symbol_id = None
                    conn.execute(
                        "INSERT INTO relationships (id, repository_id, run_id, source_file_id,"
                        " source_symbol_id, rel_type, source_line, source_col, target_symbol_id,"
                        " target_ref, call_expr, resolution_status, evidence)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            r.relationship_id,
                            rid,
                            run_id,
                            r.source_file_id,
                            r.source_symbol_id,
                            r.rel_type.value,
                            r.source_line,
                            r.source_col,
                            r.target_symbol_id,
                            r.target_ref,
                            r.call_expr,
                            r.resolution_status.value,
                            r.evidence,
                        ),
                    )

                # Map rel_path -> file_id for issues that relate to a known file
                # (discovery exclusions for directories stay NULL).
                file_id_by_rel = {
                    row["rel_path"]: row["id"]
                    for row in conn.execute(
                        "SELECT id, rel_path FROM files WHERE run_id = ?", (run_id,)
                    )
                }
                for iss in outcome.issues:
                    conn.execute(
                        "INSERT INTO analysis_errors (run_id, repository_id, file_id, severity,"
                        " category, message, rel_path, line, offset) VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            run_id,
                            rid,
                            file_id_by_rel.get(iss.rel_path),
                            iss.severity.value,
                            iss.category,
                            iss.message,
                            iss.rel_path,
                            iss.line,
                            iss.offset,
                        ),
                    )

                conn.execute(
                    "UPDATE analysis_runs SET status = ?, finished_at = ?, summary_json = ?"
                    " WHERE id = ?",
                    (
                        RunStatus.COMPLETED.value,
                        iso(finished),
                        Database.summary_to_json(summary),
                        run_id,
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - persist the failure, keep old data
            self._mark_failed(run_id, f"persistence failed: {exc}")
            raise

        return AnalysisRun(
            id=run_id,
            repository_id=rid,
            status=RunStatus.COMPLETED,
            started_at=started,
            finished_at=finished,
            summary=summary,
            error=None,
        )

    @staticmethod
    def _transaction(conn):
        class _Tx:
            def __enter__(self_inner):
                conn.execute("BEGIN IMMEDIATE")
                return conn

            def __exit__(self_inner, exc_type, exc, tb):
                if exc_type is None:
                    conn.commit()
                else:
                    conn.rollback()
                return False

        return _Tx()

    @staticmethod
    def _symbol_exists(conn, symbol_id: str) -> bool:
        cur = conn.execute("SELECT 1 FROM symbols WHERE id = ? LIMIT 1", (symbol_id,))
        return cur.fetchone() is not None

    def _mark_failed(self, run_id: str, message: str) -> None:
        try:
            conn = self.db.connect()
            conn.execute(
                "UPDATE analysis_runs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (RunStatus.FAILED.value, iso(now()), message, run_id),
            )
            conn.commit()
        except Exception:  # pragma: no cover - last-resort error path
            pass

    # -------------------------------------------------------------- queries

    def _latest_run_id(self, rid: str, conn) -> str | None:
        cur = conn.execute(
            "SELECT id FROM analysis_runs WHERE repository_id = ? AND status = 'completed'"
            " ORDER BY started_at DESC, id DESC LIMIT 1",
            (rid,),
        )
        row = cur.fetchone()
        return row["id"] if row else None

    def get_run(self, run_id: str) -> AnalysisRun:
        conn = self.db.connect()
        cur = conn.execute(
            "SELECT id, repository_id, status, started_at, finished_at, summary_json, error"
            " FROM analysis_runs WHERE id = ?",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            raise AnalysisRunNotFoundError(run_id)
        rr = _row_to_run(row)
        return AnalysisRun(
            id=rr.id,
            repository_id=rr.repository_id,
            status=RunStatus(rr.status),
            started_at=rr.started_at,
            finished_at=rr.finished_at,
            summary=Database.json_to_summary(rr.summary_json),
            error=rr.error,
        )

    def list_runs(self, rid: str, limit: int = 50) -> list[AnalysisRun]:
        conn = self.db.connect()
        cur = conn.execute(
            "SELECT id, repository_id, status, started_at, finished_at, summary_json, error"
            " FROM analysis_runs WHERE repository_id = ? ORDER BY started_at DESC LIMIT ?",
            (rid, min(limit, _MAX_PAGE)),
        )
        runs: list[AnalysisRun] = []
        for row in cur.fetchall():
            rr = _row_to_run(row)
            runs.append(
                AnalysisRun(
                    id=rr.id,
                    repository_id=rr.repository_id,
                    status=RunStatus(rr.status),
                    started_at=rr.started_at,
                    finished_at=rr.finished_at,
                    summary=Database.json_to_summary(rr.summary_json),
                    error=rr.error,
                )
            )
        return runs

    def get_errors(self, run_id: str, limit: int = 50, offset: int = 0) -> PaginatedIssues:
        conn = self.db.connect()
        # validate run exists
        self.get_run(run_id)
        cur = conn.execute("SELECT COUNT(*) AS c FROM analysis_errors WHERE run_id = ?", (run_id,))
        total = int(cur.fetchone()["c"])
        cur = conn.execute(
            "SELECT id, run_id, repository_id, file_id, severity, category, message, rel_path,"
            " line, offset FROM analysis_errors WHERE run_id = ? ORDER BY id LIMIT ? OFFSET ?",
            (run_id, min(limit, _MAX_PAGE), offset),
        )
        items = [
            AnalysisIssue(
                id=str(row["id"]),
                run_id=row["run_id"],
                severity=IssueSeverity(row["severity"]),
                category=row["category"],
                message=row["message"],
                rel_path=row["rel_path"],
                line=row["line"],
                offset=row["offset"],
            )
            for row in cur.fetchall()
        ]
        return PaginatedIssues(
            items=items, page=PageMeta(total=total, limit=min(limit, _MAX_PAGE), offset=offset)
        )

    # --------------------------------------------------------------- files

    def list_files(self, rid: str, limit: int = 50, offset: int = 0) -> PaginatedFiles:
        conn = self.db.connect()
        run_id = self._latest_run_id(rid, conn)
        if run_id is None:
            return PaginatedFiles(
                items=[], page=PageMeta(total=0, limit=min(limit, _MAX_PAGE), offset=offset)
            )
        cur = conn.execute("SELECT COUNT(*) AS c FROM files WHERE run_id = ?", (run_id,))
        total = int(cur.fetchone()["c"])
        cur = conn.execute(
            "SELECT id, repository_id, run_id, rel_path, module_name, file_type, size_bytes,"
            " content_hash, parse_status, error_message, error_line, error_offset FROM files"
            " WHERE run_id = ? ORDER BY rel_path LIMIT ? OFFSET ?",
            (run_id, min(limit, _MAX_PAGE), offset),
        )
        items = [self._file_record(row) for row in cur.fetchall()]
        return PaginatedFiles(
            items=items, page=PageMeta(total=total, limit=min(limit, _MAX_PAGE), offset=offset)
        )

    @staticmethod
    def _file_record(row) -> FileRecord:
        fr = _row_to_file(row)
        return FileRecord(
            id=fr.id,
            repository_id=fr.repository_id,
            rel_path=fr.rel_path,
            module_name=fr.module_name,
            file_type=fr.file_type,
            size_bytes=fr.size_bytes,
            content_hash=fr.content_hash,
            parse_status=ParseStatus(fr.parse_status),
            error_message=fr.error_message,
            error_line=fr.error_line,
            error_offset=fr.error_offset,
        )

    def get_file_detail(
        self,
        rid: str,
        rel_path: str,
        *,
        include_source: bool = False,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> FileDetail:
        normalized = validate_rel_path(rel_path)
        conn = self.db.connect()
        run_id = self._latest_run_id(rid, conn)
        if run_id is None:
            raise FileNotFoundError_(normalized)
        cur = conn.execute(
            "SELECT id, repository_id, run_id, rel_path, module_name, file_type, size_bytes,"
            " content_hash, parse_status, error_message, error_line, error_offset FROM files"
            " WHERE run_id = ? AND rel_path = ?",
            (run_id, normalized),
        )
        row = cur.fetchone()
        if not row:
            raise FileNotFoundError_(normalized)
        base = self._file_record(row)
        detail = FileDetail(**base.model_dump())

        if not include_source:
            return detail

        root = self.repo_svc.get_root(rid)
        candidate = root / normalized
        if not is_within_root(root, candidate):
            raise PathTraversalError(normalized)
        if not candidate.is_file():
            detail.source_available = False
            return detail
        rr = read_source_file(candidate, self.settings.max_file_bytes)
        detail.source_available = rr.ok
        if not rr.ok:
            detail.error_message = rr.error
            return detail
        detail.content_changed_since_analysis = rr.content_hash != base.content_hash
        text = rr.content or ""
        if start_line is not None or end_line is not None:
            lines = text.splitlines()
            lo = max(1, start_line or 1)
            hi = min(len(lines), end_line or len(lines))
            if hi < lo:
                detail.content = ""
                detail.start_line = lo
                detail.end_line = lo - 1
            else:
                detail.content = "\n".join(lines[lo - 1 : hi])
                detail.start_line = lo
                detail.end_line = hi
        else:
            detail.content = text
            detail.start_line = 1
            detail.end_line = len(text.splitlines())
        return detail

    # ------------------------------------------------------------ symbols

    def list_symbols(
        self,
        rid: str,
        *,
        file_id: str | None = None,
        kind: SymbolKind | None = None,
        name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedSymbols:
        conn = self.db.connect()
        run_id = self._latest_run_id(rid, conn)
        if run_id is None:
            return PaginatedSymbols(
                items=[], page=PageMeta(total=0, limit=min(limit, _MAX_PAGE), offset=offset)
            )
        where = ["run_id = ?"]
        params: list[object] = [run_id]
        if file_id:
            where.append("file_id = ?")
            params.append(file_id)
        if kind:
            where.append("kind = ?")
            params.append(kind.value)
        if name:
            where.append("name = ?")
            params.append(name)
        clause = " AND ".join(where)
        cur = conn.execute(f"SELECT COUNT(*) AS c FROM symbols WHERE {clause}", params)
        total = int(cur.fetchone()["c"])
        cur = conn.execute(
            f"SELECT * FROM symbols WHERE {clause} ORDER BY file_id, start_line, ordinal"
            " LIMIT ? OFFSET ?",
            [*params, min(limit, _MAX_PAGE), offset],
        )
        items = [self._symbol_record(conn, row) for row in cur.fetchall()]
        return PaginatedSymbols(
            items=items, page=PageMeta(total=total, limit=min(limit, _MAX_PAGE), offset=offset)
        )

    def _symbol_record(self, conn, row) -> SymbolRecord:
        sr = _row_to_symbol(row)
        cur = conn.execute("SELECT rel_path FROM files WHERE id = ?", (sr.file_id,))
        frow = cur.fetchone()
        rel_path = frow["rel_path"] if frow else ""
        params_data = _load_json(sr.parameters_json) or []
        return SymbolRecord(
            id=sr.id,
            repository_id=sr.repository_id,
            file_id=sr.file_id,
            rel_path=rel_path,
            name=sr.name,
            qualified_name=sr.qualified_name,
            kind=SymbolKind(sr.kind),
            parent_id=sr.parent_id,
            docstring=sr.docstring,
            signature=sr.signature,
            parameters=[ParameterModel.model_validate(p) for p in params_data],
            return_annotation=sr.return_annotation,
            bases=_load_json(sr.bases_json) or [],
            decorators=_load_json(sr.decorators_json) or [],
            start_line=sr.start_line,
            end_line=sr.end_line,
            ordinal=sr.ordinal,
        )

    def get_symbol(self, symbol_id: str) -> SymbolRecord:
        conn = self.db.connect()
        cur = conn.execute("SELECT * FROM symbols WHERE id = ?", (symbol_id,))
        row = cur.fetchone()
        if not row:
            raise SymbolNotFoundError(symbol_id)
        return self._symbol_record(conn, row)

    def get_symbol_children(self, symbol_id: str, limit: int = 200) -> list[SymbolRecord]:
        conn = self.db.connect()
        self.get_symbol(symbol_id)  # 404 if unknown
        cur = conn.execute(
            "SELECT * FROM symbols WHERE parent_id = ? ORDER BY start_line, ordinal LIMIT ?",
            (symbol_id, min(limit, _MAX_PAGE)),
        )
        return [self._symbol_record(conn, row) for row in cur.fetchall()]

    def get_symbol_parent(self, symbol_id: str) -> SymbolRecord | None:
        record = self.get_symbol(symbol_id)
        if record.parent_id is None:
            return None
        conn = self.db.connect()
        cur = conn.execute("SELECT * FROM symbols WHERE id = ?", (record.parent_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._symbol_record(conn, row)

    # ------------------------------------------------------- relationships

    def list_relationships(
        self,
        rid: str,
        *,
        rel_type: RelationshipType | None = None,
        status: ResolutionStatus | None = None,
        symbol_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedRelationships:
        conn = self.db.connect()
        run_id = self._latest_run_id(rid, conn)
        if run_id is None:
            return PaginatedRelationships(
                items=[], page=PageMeta(total=0, limit=min(limit, _MAX_PAGE), offset=offset)
            )
        where = ["run_id = ?"]
        params: list[object] = [run_id]
        if rel_type:
            where.append("rel_type = ?")
            params.append(rel_type.value)
        if status:
            where.append("resolution_status = ?")
            params.append(status.value)
        if symbol_id:
            where.append("(source_symbol_id = ? OR target_symbol_id = ?)")
            params.extend([symbol_id, symbol_id])
        clause = " AND ".join(where)
        # file_id -> rel_path for this run, so records carry their source path
        rel_path_by_file = {
            row["id"]: row["rel_path"]
            for row in conn.execute("SELECT id, rel_path FROM files WHERE run_id = ?", (run_id,))
        }
        cur = conn.execute(f"SELECT COUNT(*) AS c FROM relationships WHERE {clause}", params)
        total = int(cur.fetchone()["c"])
        cur = conn.execute(
            f"SELECT * FROM relationships WHERE {clause} ORDER BY source_file_id, source_line"
            " LIMIT ? OFFSET ?",
            [*params, min(limit, _MAX_PAGE), offset],
        )
        items = []
        for row in cur.fetchall():
            record = self._rel_record(row)
            record.source_rel_path = rel_path_by_file.get(record.source_file_id, "")
            items.append(record)
        return PaginatedRelationships(
            items=items, page=PageMeta(total=total, limit=min(limit, _MAX_PAGE), offset=offset)
        )

    @staticmethod
    def _rel_record(row) -> RelationshipRecord:
        rr = _row_to_rel(row)
        return RelationshipRecord(
            id=rr.id,
            repository_id=rr.repository_id,
            source_file_id=rr.source_file_id,
            source_symbol_id=rr.source_symbol_id,
            source_rel_path="",  # filled by list_relationships from the run's files
            source_line=rr.source_line,
            source_col=rr.source_col,
            rel_type=RelationshipType(rr.rel_type),
            target_symbol_id=rr.target_symbol_id,
            target_ref=rr.target_ref,
            call_expr=rr.call_expr,
            resolution_status=ResolutionStatus(rr.resolution_status),
            evidence=rr.evidence,
        )

    # ------------------------------------------------------ graph loading

    def load_graph_facts(self, rid: str):
        """Load files, symbols, relationships of the latest completed run."""
        conn = self.db.connect()
        run_id = self._latest_run_id(rid, conn)
        if run_id is None:
            return [], [], []
        files = [
            _row_to_file(row)
            for row in conn.execute(
                "SELECT * FROM files WHERE run_id = ? ORDER BY rel_path", (run_id,)
            ).fetchall()
        ]
        symbols = [
            _row_to_symbol(row)
            for row in conn.execute(
                "SELECT * FROM symbols WHERE run_id = ? ORDER BY file_id, start_line, ordinal",
                (run_id,),
            ).fetchall()
        ]
        relationships = [
            _row_to_rel(row)
            for row in conn.execute(
                "SELECT * FROM relationships WHERE run_id = ? ORDER BY source_file_id, source_line",
                (run_id,),
            ).fetchall()
        ]
        return files, symbols, relationships

    def find_repository_for_node(self, node_id: str) -> str | None:
        """Resolve the owning repository id of a graph node id, if known.

        Node ids are deterministic hashes, so the owning repository is looked
        up in the latest run's files and symbols (repository nodes carry the
        repository id verbatim as ``repo_<repo_id>``).
        """
        if node_id.startswith("repo_"):
            candidate = node_id[len("repo_") :]
            conn = self.db.connect()
            cur = conn.execute("SELECT 1 FROM repositories WHERE id = ?", (candidate,))
            return candidate if cur.fetchone() else None
        conn = self.db.connect()
        cur = conn.execute("SELECT repository_id, run_id FROM files WHERE id = ?", (node_id,))
        row = cur.fetchone()
        if row is None:
            cur = conn.execute("SELECT repository_id, run_id FROM symbols WHERE id = ?", (node_id,))
            row = cur.fetchone()
        if row is None:
            return None
        run_id = row["run_id"]
        # Only expose nodes belonging to the latest run of that repository.
        latest = self._latest_run_id(row["repository_id"], conn)
        if latest is None or latest != run_id:
            return None
        return row["repository_id"]
