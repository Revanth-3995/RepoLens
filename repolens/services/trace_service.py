"""Cross-file tracing service (Phase 2, P2-06)."""

from __future__ import annotations

from repolens.analysis.tracing import connect_symbols, trace_file_dependents, trace_symbol
from repolens.config import Settings
from repolens.core.errors import (
    AnalysisNotAvailableError,
    FileNotFoundError_,
    SymbolNotFoundError,
)
from repolens.core.models import (
    Direction,
    RelationshipType,
    SymbolKind,
    TraceConnection,
    TraceResult,
)
from repolens.services.analysis_service import AnalysisService, validate_rel_path


class TraceService:
    """Load persisted relationships and run bounded, deterministic traversal."""

    def __init__(self, analysis: AnalysisService, settings: Settings) -> None:
        self.analysis = analysis
        self.settings = settings

    def _require_run(self, repository_id: str) -> None:
        self.analysis.repo_svc.get_root(repository_id)
        if self.analysis.latest_run_id(repository_id) is None:
            raise AnalysisNotAvailableError(
                f"repository {repository_id} has no completed analysis run"
            )

    def _symbol(self, repository_id: str, symbol_id: str):
        symbol = self.analysis.get_symbol(symbol_id)
        if symbol.repository_id != repository_id:
            raise SymbolNotFoundError(symbol_id)
        return symbol

    def trace(
        self,
        repository_id: str,
        symbol_id: str,
        *,
        direction: Direction = Direction.OUT,
        rel_types: set[RelationshipType] | None = None,
        max_depth: int | None = None,
        max_nodes: int | None = None,
    ) -> TraceResult:
        """Trace forward, reverse, or both directions from one symbol."""
        self._require_run(repository_id)
        symbol = self._symbol(repository_id, symbol_id)
        symbols = self.analysis.list_all_symbols(repository_id)
        relationships = self.analysis.list_all_relationships(repository_id)
        return trace_symbol(
            repository_id,
            symbol,
            symbols,
            relationships,
            direction=direction,
            rel_types=rel_types,
            max_depth=max_depth or self.settings.trace_max_depth,
            max_nodes=max_nodes or self.settings.trace_max_nodes,
        )

    def connect(
        self,
        repository_id: str,
        source_symbol_id: str,
        target_symbol_id: str,
        *,
        direction: Direction = Direction.OUT,
        max_depth: int | None = None,
        max_nodes: int | None = None,
    ) -> TraceConnection:
        """Find a bounded static relationship path between two symbols."""
        self._require_run(repository_id)
        source = self._symbol(repository_id, source_symbol_id)
        target = self._symbol(repository_id, target_symbol_id)
        symbols = self.analysis.list_all_symbols(repository_id)
        relationships = self.analysis.list_all_relationships(repository_id)
        return connect_symbols(
            repository_id,
            source,
            target,
            symbols,
            relationships,
            direction=direction,
            max_depth=max_depth or self.settings.trace_max_depth,
            max_nodes=max_nodes or self.settings.trace_max_nodes,
        )

    def dependents(
        self,
        repository_id: str,
        rel_path: str,
        *,
        max_depth: int | None = None,
        max_nodes: int | None = None,
    ) -> TraceResult:
        """Reverse-import trace for a module: who depends on this file?"""
        self._require_run(repository_id)
        normalized = validate_rel_path(rel_path)
        symbols = self.analysis.list_all_symbols(repository_id)
        module = next(
            (
                symbol
                for symbol in symbols
                if symbol.rel_path == normalized and symbol.kind is SymbolKind.MODULE
            ),
            None,
        )
        if module is None:
            raise FileNotFoundError_(normalized)
        relationships = self.analysis.list_all_relationships(repository_id)
        return trace_file_dependents(
            repository_id,
            module,
            symbols,
            relationships,
            max_depth=max_depth or self.settings.trace_max_depth,
            max_nodes=max_nodes or self.settings.trace_max_nodes,
        )
