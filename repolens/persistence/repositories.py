"""Data-access layer for repositories, runs, files, symbols, relationships, errors."""

from __future__ import annotations

from datetime import datetime

from repolens.persistence.database import parse_iso
from repolens.persistence.models import (
    ErrRow,
    FileRow,
    RelRow,
    RepoRow,
    RunRow,
    SymbolRow,
)


def _row_to_repo(r) -> RepoRow:
    return RepoRow(
        id=r["id"],
        name=r["name"],
        root_path=r["root_path"],
        registered_at=parse_iso(r["registered_at"]) or datetime.utcnow(),
    )


def _row_to_run(r) -> RunRow:
    return RunRow(
        id=r["id"],
        repository_id=r["repository_id"],
        status=r["status"],
        started_at=parse_iso(str(r["started_at"])) or datetime.utcnow(),
        finished_at=parse_iso(str(r["finished_at"])) if r["finished_at"] else None,
        summary_json=r["summary_json"],
        error=r["error"],
    )


def _row_to_file(r) -> FileRow:
    return FileRow(
        id=r["id"],
        repository_id=r["repository_id"],
        run_id=r["run_id"],
        rel_path=r["rel_path"],
        module_name=r["module_name"],
        file_type=r["file_type"],
        size_bytes=r["size_bytes"],
        content_hash=r["content_hash"],
        parse_status=r["parse_status"],
        error_message=r["error_message"],
        error_line=r["error_line"],
        error_offset=r["error_offset"],
    )


def _row_to_symbol(r) -> SymbolRow:
    return SymbolRow(
        id=r["id"],
        repository_id=r["repository_id"],
        run_id=r["run_id"],
        file_id=r["file_id"],
        name=r["name"],
        qualified_name=r["qualified_name"],
        kind=r["kind"],
        parent_id=r["parent_id"],
        docstring=r["docstring"],
        signature=r["signature"],
        parameters_json=r["parameters_json"],
        return_annotation=r["return_annotation"],
        bases_json=r["bases_json"],
        decorators_json=r["decorators_json"],
        start_line=r["start_line"],
        end_line=r["end_line"],
        ordinal=r["ordinal"],
    )


def _row_to_rel(r) -> RelRow:
    return RelRow(
        id=r["id"],
        repository_id=r["repository_id"],
        run_id=r["run_id"],
        source_file_id=r["source_file_id"],
        source_symbol_id=r["source_symbol_id"],
        rel_type=r["rel_type"],
        source_line=r["source_line"],
        source_col=r["source_col"],
        target_symbol_id=r["target_symbol_id"],
        target_ref=r["target_ref"],
        call_expr=r["call_expr"],
        resolution_status=r["resolution_status"],
        evidence=r["evidence"],
    )


def _row_to_err(r) -> ErrRow:
    return ErrRow(
        id=r["id"],
        run_id=r["run_id"],
        repository_id=r["repository_id"],
        file_id=r["file_id"],
        severity=r["severity"],
        category=r["category"],
        message=r["message"],
        rel_path=r["rel_path"],
        line=r["line"],
        offset=r["offset"],
    )
