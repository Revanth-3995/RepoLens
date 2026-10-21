"""Lexical search service (Phase 2, P2-05).

Search covers stored symbols and the current source text of the latest
completed run. Source reading is bounded by ``search_max_files`` and
``max_file_bytes``; every truncation is reported in the response.
"""

from __future__ import annotations

from repolens.analysis.search import (
    combine_hits,
    paginate,
    search_symbols,
    search_text,
    validate_identifier_query,
)
from repolens.config import Settings
from repolens.core.errors import AnalysisNotAvailableError, InvalidRequestError
from repolens.core.models import (
    PageMeta,
    PaginatedSearchHits,
    ParseStatus,
    SearchMode,
    SymbolKind,
)
from repolens.services.analysis_service import AnalysisService

_TEXT_MODES = {SearchMode.ALL, SearchMode.TEXT, SearchMode.TOKEN}


class SearchService:
    """Search stored symbols and current source lines deterministically."""

    def __init__(self, analysis: AnalysisService, settings: Settings) -> None:
        self.analysis = analysis
        self.settings = settings

    def search(
        self,
        repository_id: str,
        query: str,
        *,
        mode: SearchMode = SearchMode.ALL,
        kind: SymbolKind | None = None,
        path_prefix: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedSearchHits:
        """Run a symbol/text/token query against the latest completed run."""
        self.analysis.repo_svc.get_root(repository_id)
        text = query.strip()
        if not text:
            raise InvalidRequestError("search query must not be empty")
        if mode is SearchMode.IDENTIFIER and not validate_identifier_query(text):
            raise InvalidRequestError(
                "identifier search requires a dotted Python identifier"
                " (letters, digits, underscores, separated by '.')"
            )
        if self.analysis.latest_run_id(repository_id) is None:
            raise AnalysisNotAvailableError(
                f"repository {repository_id} has no completed analysis run"
            )

        symbols = self.analysis.list_all_symbols(repository_id)
        symbol_hits = []
        if mode in (SearchMode.ALL, SearchMode.SYMBOL, SearchMode.IDENTIFIER):
            symbol_hits = search_symbols(text, symbols, kind=kind, path_prefix=path_prefix)

        text_hits = []
        truncated = False
        if mode in _TEXT_MODES:
            files = [
                file
                for file in self.analysis.list_all_files(repository_id)
                if file.parse_status is ParseStatus.PARSED
            ]
            if len(files) > self.settings.search_max_files:
                files = files[: self.settings.search_max_files]
                truncated = True
            contents = []
            for file in files:
                detail = self.analysis.get_file_detail(
                    repository_id, file.rel_path, include_source=True
                )
                if detail.source_available and detail.content is not None:
                    contents.append((file, detail.content))
            found, text_truncated = search_text(
                text,
                contents,
                symbols,
                mode=SearchMode.TEXT if mode is SearchMode.ALL else mode,
                path_prefix=path_prefix,
                kind=kind,
                max_per_file=50,
                max_hits=self.settings.search_max_hits,
            )
            text_hits = found
            truncated = truncated or text_truncated

        combined, combined_truncated = combine_hits(
            symbol_hits, text_hits, max_hits=self.settings.search_max_hits
        )
        truncated = truncated or combined_truncated
        items, total = paginate(combined, limit=limit, offset=offset)
        return PaginatedSearchHits(
            items=items,
            page=PageMeta(total=total, limit=limit, offset=offset),
            truncated=truncated,
        )
