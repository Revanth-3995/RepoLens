"""Analysis pipeline: discovery -> read -> parse -> extract -> resolve -> validate.

Pure in-memory: given a repository root and settings, returns an
``AnalysisOutcome`` with all extracted facts and computed summary inputs.
Persistence is the analysis service's job. The analyzed repository's code is
never executed, imported, or modified.
"""

from __future__ import annotations

import time
from pathlib import Path

from repolens.analysis.imports_calls import extract_imports_and_calls
from repolens.analysis.modules import module_name_for
from repolens.analysis.python_parser import parse_source
from repolens.analysis.resolver import Resolver
from repolens.analysis.symbols import extract_symbols
from repolens.config import Settings
from repolens.core.errors import ParseError
from repolens.core.identifiers import file_id_for, relationship_id_for, symbol_id_for
from repolens.core.models import (
    AnalysisIssueDraft,
    AnalysisOutcome,
    FileAnalysis,
    IssueSeverity,
    ParseStatus,
    SymbolKind,
)
from repolens.ingestion.discovery import discover_python_files
from repolens.ingestion.reader import read_source_file

# Bound on discovery issues recorded per run (counts remain exact).
_MAX_DISCOVERY_ISSUES = 200


def _line_count(source: str) -> int:
    return len(source.splitlines()) or 1


def _assign_ids(repository_id: str, files: list[FileAnalysis]) -> None:
    """Assign deterministic file/symbol ids and parent links."""
    for f in files:
        f.file_id = file_id_for(repository_id, f.rel_path)
        for s in f.symbols:
            s.symbol_id = symbol_id_for(
                repository_id, f.rel_path, s.kind.value, s.qualified_name, s.ordinal
            )
            s.file_id = f.file_id
    for f in files:
        # map qualified -> symbol for parent lookup within the file
        by_q: dict[str, str] = {}
        for s in f.symbols:
            by_q.setdefault(s.qualified_name, s.symbol_id)
        for s in f.symbols:
            if s.parent_qualified is None:
                s.parent_id = None
            else:
                s.parent_id = by_q.get(s.parent_qualified)


class AnalysisPipeline:
    """Pure in-memory analysis pipeline."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def analyze(self, repository_id: str, root_path: Path) -> AnalysisOutcome:
        started = time.perf_counter()
        root = root_path.resolve()
        outcome = AnalysisOutcome(repository_id=repository_id)

        # -------------------------------------------------------- discovery
        discovery = discover_python_files(root, self.settings)
        outcome.discovered_count = discovery.discovered_count
        outcome.truncated = discovery.truncated
        outcome.excluded_directories = len(discovery.excluded_dirs)
        outcome.excluded_files = len(discovery.excluded_files)

        recorded = 0
        suppressed = 0
        for entry in [*discovery.excluded_dirs, *discovery.excluded_files]:
            is_dir = entry in discovery.excluded_dirs
            if recorded >= _MAX_DISCOVERY_ISSUES:
                suppressed += 1
                continue
            recorded += 1
            outcome.issues.append(
                AnalysisIssueDraft(
                    severity=IssueSeverity.WARNING,
                    category="discovery",
                    message=f"{'directory' if is_dir else 'file'} not analyzed: {entry.reason}",
                    rel_path=entry.rel_path,
                )
            )
        if suppressed:
            outcome.issues.append(
                AnalysisIssueDraft(
                    severity=IssueSeverity.WARNING,
                    category="discovery",
                    message=f"{suppressed} additional discovery exclusions suppressed from issue list"
                    " (counts in summary remain exact)",
                )
            )
        if discovery.truncated:
            outcome.issues.append(
                AnalysisIssueDraft(
                    severity=IssueSeverity.ERROR,
                    category="limit_exceeded",
                    message=f"file limit reached ({self.settings.max_files}); discovery truncated",
                )
            )

        # ------------------------------------------------- read/parse/extract
        for df in discovery.files:
            module_name, _is_pkg = module_name_for(df.rel_path)
            rd = read_source_file(df.path, self.settings.max_file_bytes)
            if not rd.ok:
                outcome.files.append(
                    FileAnalysis(
                        rel_path=df.rel_path,
                        module_name=module_name,
                        size_bytes=rd.size_bytes,
                        content_hash=rd.content_hash,
                        parse_status=ParseStatus.READ_ERROR,
                        error_message=rd.error,
                    )
                )
                outcome.issues.append(
                    AnalysisIssueDraft(
                        severity=IssueSeverity.ERROR,
                        category="read_error",
                        message=rd.error or "read error",
                        rel_path=df.rel_path,
                    )
                )
                continue

            source = rd.content or ""
            try:
                tree = parse_source(source, df.rel_path)
            except ParseError as exc:
                outcome.files.append(
                    FileAnalysis(
                        rel_path=df.rel_path,
                        module_name=module_name,
                        size_bytes=rd.size_bytes,
                        content_hash=rd.content_hash,
                        parse_status=ParseStatus.PARSE_ERROR,
                        error_message=exc.message,
                        error_line=exc.line,
                        error_offset=exc.offset,
                    )
                )
                outcome.issues.append(
                    AnalysisIssueDraft(
                        severity=IssueSeverity.ERROR,
                        category="parse_error",
                        message=exc.message,
                        rel_path=df.rel_path,
                        line=exc.line,
                        offset=exc.offset,
                    )
                )
                continue

            table = extract_symbols(tree, module_name, _line_count(source))
            extraction = extract_imports_and_calls(tree, table.node_to_qualified, module_name)
            outcome.files.append(
                FileAnalysis(
                    rel_path=df.rel_path,
                    module_name=module_name,
                    size_bytes=rd.size_bytes,
                    content_hash=rd.content_hash,
                    parse_status=ParseStatus.PARSED,
                    symbols=table.drafts,
                    imports=extraction.imports,
                    calls=extraction.calls,
                    star_import=extraction.has_star_import,
                    local_names=extraction.local_names,
                )
            )

        # ---------------------------------------------------------- resolve
        _assign_ids(repository_id, outcome.files)
        resolver = Resolver(repository_id, outcome.files)
        relationships = [
            *resolver.resolve_imports(),
            *resolver.resolve_inherits(),
            *resolver.resolve_calls(),
        ]

        # stable relationship ids (also the in-run dedupe key)
        seen: set[str] = set()
        for rel in relationships:
            source_symbol = rel.source_symbol_id
            if source_symbol is None:
                source_symbol = _module_symbol_id_of(outcome.files, rel.source_file_id)
                rel.source_symbol_id = source_symbol
            rel.relationship_id = relationship_id_for(
                repository_id,
                rel.source_file_id,
                source_symbol,
                rel.rel_type.value,
                rel.target_symbol_id,
                rel.target_ref,
                rel.source_line,
                rel.source_col,
            )
            if rel.relationship_id in seen:
                continue  # dedupe: identical provenance => identical fact
            seen.add(rel.relationship_id)
            outcome.relationships.append(rel)

        outcome.duration_seconds = time.perf_counter() - started
        return outcome


def _module_symbol_id_of(files: list[FileAnalysis], file_id: str) -> str | None:
    for f in files:
        if f.file_id == file_id:
            for s in f.symbols:
                if s.kind is SymbolKind.MODULE:
                    return s.symbol_id
    return None
