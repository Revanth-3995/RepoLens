"""Application service for CFG, data flow, and explanations (Phase 2).

The service is the I/O boundary:

1. load persisted facts (symbols, files, relationships, metrics),
2. re-read the current source only when a CFG/data-flow/statement explanation is
   requested,
3. call the pure analysis functions in ``repolens.analysis.*``,
4. return API-ready pydantic models.

CFGs and data-flow results are **computed on demand** from the stored symbol's
file and line identity. They are not persisted: they are large, derivable from
source, and the stored run remains the authority for what was analyzed. If the
file changed after the run, the result carries
``source_changed_since_analysis=True`` and the explanation states it.
"""

from __future__ import annotations

import ast

from repolens.analysis.cfg import CFGWithFacts, build_function_cfg_with_facts
from repolens.analysis.dataflow import analyze_dataflow
from repolens.analysis.entrypoints import detect_entry_points
from repolens.analysis.explanations import (
    explain_file,
    explain_folder,
    explain_function,
    explain_project,
    explain_statement,
)
from repolens.analysis.function_locator import find_function_node
from repolens.analysis.python_parser import parse_source
from repolens.analysis.symbols import extract_symbols
from repolens.config import Settings
from repolens.core.errors import (
    AnalysisNotAvailableError,
    InvalidRequestError,
    ParseError,
    SourceUnavailableError,
)
from repolens.core.models import (
    DataFlowResult,
    EntryPoint,
    Explanation,
    FileDetail,
    FunctionCFG,
    SymbolKind,
    SymbolRecord,
)
from repolens.services.analysis_service import AnalysisService, validate_rel_path
from repolens.services.architecture_service import ArchitectureService


class ComprehensionService:
    """Load facts, run on-demand structural analyses, and build explanations."""

    def __init__(
        self, analysis: AnalysisService, settings: Settings, architecture: ArchitectureService
    ) -> None:
        self.analysis = analysis
        self.settings = settings
        self.architecture = architecture

    # --------------------------------------------------------------- loading

    def _require_run(self, repository_id: str) -> None:
        self.analysis.repo_svc.get_root(repository_id)
        if self.analysis.latest_run_id(repository_id) is None:
            raise AnalysisNotAvailableError(
                f"repository {repository_id} has no completed analysis run"
            )

    def _load_function(
        self, symbol_id: str
    ) -> tuple[SymbolRecord, ast.FunctionDef | ast.AsyncFunctionDef, FileDetail, list[str]]:
        symbol = self.analysis.get_symbol(symbol_id)
        if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
            raise InvalidRequestError(
                f"symbol {symbol_id} is a {symbol.kind.value}, not a function or method"
            )
        detail = self.analysis.get_file_detail(
            symbol.repository_id, symbol.rel_path, include_source=True
        )
        if not detail.source_available or detail.content is None:
            raise SourceUnavailableError(
                f"source for {symbol.rel_path} is not readable; cannot compute on-demand analysis"
            )
        try:
            tree = parse_source(detail.content, symbol.rel_path)
        except ParseError as exc:
            raise SourceUnavailableError(
                f"current source for {symbol.rel_path} no longer parses: {exc.message}"
            ) from exc
        lines = detail.content.splitlines()
        table = extract_symbols(tree, detail.module_name, len(lines))
        node = find_function_node(tree, table, symbol)
        if node is None:
            raise SourceUnavailableError(
                f"function {symbol.qualified_name!r} is no longer present in"
                f" {symbol.rel_path}; re-run analysis"
            )
        return symbol, node, detail, lines

    def _build(self, symbol_id: str) -> tuple[SymbolRecord, FileDetail, CFGWithFacts]:
        symbol, node, detail, lines = self._load_function(symbol_id)
        built = build_function_cfg_with_facts(
            node,
            lines,
            symbol_id=symbol.id,
            qualified_name=symbol.qualified_name,
            file_id=symbol.file_id,
            rel_path=symbol.rel_path,
            source_changed_since_analysis=detail.content_changed_since_analysis,
            max_nodes=self.settings.cfg_max_nodes,
            max_edges=self.settings.cfg_max_edges,
            max_depth=self.settings.cfg_max_depth,
        )
        return symbol, detail, built

    # ------------------------------------------------------------- analyses

    def cfg_for_symbol(self, symbol_id: str) -> FunctionCFG:
        """Build the CFG of one function from current source and stored identity."""
        _symbol, _detail, built = self._build(symbol_id)
        return built.cfg

    def dataflow_for_symbol(self, symbol_id: str) -> DataFlowResult:
        """Build the CFG and run the bounded local reaching-definition analysis."""
        _symbol, _detail, built = self._build(symbol_id)
        return analyze_dataflow(
            built,
            max_definitions=self.settings.dataflow_max_definitions,
            max_uses=self.settings.dataflow_max_uses,
            max_iterations=self.settings.dataflow_max_iterations,
        )

    # --------------------------------------------------------- explanations

    def statement_explanation(self, repository_id: str, symbol_id: str, line: int) -> Explanation:
        """Explain the statement covering ``line`` of a function."""
        self._require_run(repository_id)
        symbol = self.analysis.get_symbol(symbol_id)
        if symbol.repository_id != repository_id:
            raise InvalidRequestError(
                f"symbol {symbol_id} does not belong to repository {repository_id}"
            )
        if line < symbol.start_line or line > symbol.end_line:
            raise InvalidRequestError(
                f"line {line} is outside {symbol.qualified_name} "
                f"({symbol.start_line}-{symbol.end_line})"
            )
        symbol, _detail, built = self._build(symbol_id)
        dataflow = analyze_dataflow(
            built,
            max_definitions=self.settings.dataflow_max_definitions,
            max_uses=self.settings.dataflow_max_uses,
            max_iterations=self.settings.dataflow_max_iterations,
        )
        return explain_statement(symbol, built.cfg, dataflow, line)

    def function_explanation(self, repository_id: str, symbol_id: str) -> Explanation:
        """Explain one function from metrics, CFG, data flow, and relationships."""
        self._require_run(repository_id)
        existing = self.analysis.get_symbol(symbol_id)
        if existing.repository_id != repository_id:
            raise InvalidRequestError(
                f"symbol {symbol_id} does not belong to repository {repository_id}"
            )
        symbol, _detail, built = self._build(symbol_id)
        cfg = built.cfg
        dataflow = analyze_dataflow(
            built,
            max_definitions=self.settings.dataflow_max_definitions,
            max_uses=self.settings.dataflow_max_uses,
            max_iterations=self.settings.dataflow_max_iterations,
        )
        metrics = self.analysis.get_function_metrics(symbol.id)
        relationships = self.analysis.list_all_relationships(symbol.repository_id)
        return explain_function(
            symbol,
            metrics,
            cfg,
            dataflow,
            relationships,
            complexity_threshold=self.settings.complexity_warning_threshold,
        )

    def file_explanation(self, repository_id: str, rel_path: str) -> Explanation:
        """Explain one analyzed file from persisted facts."""
        self._require_run(repository_id)
        detail = self.analysis.get_file_detail(repository_id, rel_path, include_source=True)
        all_symbols = self.analysis.list_all_symbols(repository_id)
        symbols = [s for s in all_symbols if s.file_id == detail.id]
        relationships = [
            rel
            for rel in self.analysis.list_all_relationships(repository_id)
            if rel.source_file_id == detail.id or rel.target_symbol_id in {s.id for s in symbols}
        ]
        metrics = [
            metric
            for metric in self.analysis.list_all_function_metrics(repository_id)
            if metric.file_id == detail.id
        ]
        entry_points: list[EntryPoint] = []
        entry_error: str | None = None
        if detail.source_available and detail.content is not None:
            try:
                tree = parse_source(detail.content, detail.rel_path)
                entry_points = detect_entry_points(tree, detail.rel_path)
            except ParseError as exc:
                entry_error = f"entry-point detection failed: {exc.message}"
        return explain_file(
            detail,
            symbols,
            relationships,
            metrics,
            entry_points,
            all_symbols=all_symbols,
            entry_point_error=entry_error,
        )

    def folder_explanation(self, repository_id: str, prefix: str) -> Explanation:
        """Explain a path-based folder grouping."""
        self._require_run(repository_id)
        raw_prefix = prefix.strip()
        normalized = "" if raw_prefix in {"", "."} else validate_rel_path(raw_prefix)
        files = self.analysis.list_all_files(repository_id)
        if normalized:
            files = [
                file
                for file in files
                if file.rel_path == normalized or file.rel_path.startswith(normalized + "/")
            ]
        if not files:
            raise InvalidRequestError(f"no analyzed files under {normalized or '.'!r}")
        file_ids = {file.id for file in files}
        all_symbols = self.analysis.list_all_symbols(repository_id)
        symbols = [symbol for symbol in all_symbols if symbol.file_id in file_ids]
        relationships = self.analysis.list_all_relationships(repository_id)
        metrics = self.analysis.list_all_function_metrics(repository_id)
        architecture = self.architecture.summary(repository_id)
        return explain_folder(
            normalized,
            files,
            symbols,
            relationships,
            metrics,
            architecture,
            all_symbols=all_symbols,
        )

    def project_explanation(self, repository_id: str) -> Explanation:
        """Explain the repository from all persisted facts and architecture."""
        self._require_run(repository_id)
        files = self.analysis.list_all_files(repository_id)
        symbols = self.analysis.list_all_symbols(repository_id)
        metrics = self.analysis.list_all_function_metrics(repository_id)
        architecture = self.architecture.summary(repository_id)
        return explain_project(repository_id, files, symbols, metrics, architecture)
