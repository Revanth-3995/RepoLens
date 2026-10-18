"""Dataclasses representing SQLite rows (return types for DAL helpers)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class RepoRow:
    id: str
    name: str
    root_path: str
    registered_at: datetime


@dataclass
class RunRow:
    id: str
    repository_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    summary_json: str | None
    error: str | None


@dataclass
class FileRow:
    id: str
    repository_id: str
    run_id: str
    rel_path: str
    module_name: str
    file_type: str
    size_bytes: int
    content_hash: str
    parse_status: str
    error_message: str | None
    error_line: int | None
    error_offset: int | None


@dataclass
class SymbolRow:
    id: str
    repository_id: str
    run_id: str
    file_id: str
    name: str
    qualified_name: str
    kind: str
    parent_id: str | None
    docstring: str | None
    signature: str | None
    parameters_json: str | None
    return_annotation: str | None
    bases_json: str | None
    decorators_json: str | None
    start_line: int
    end_line: int
    ordinal: int


@dataclass
class RelRow:
    id: str
    repository_id: str
    run_id: str
    source_file_id: str
    source_symbol_id: str | None
    rel_type: str
    source_line: int
    source_col: int
    target_symbol_id: str | None
    target_ref: str
    call_expr: str | None
    resolution_status: str
    evidence: str


@dataclass
class ErrRow:
    id: int
    run_id: str
    repository_id: str
    file_id: str | None
    severity: str
    category: str
    message: str
    rel_path: str | None
    line: int | None
    offset: int | None
