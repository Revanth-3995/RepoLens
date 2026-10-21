"""Deterministic lexical search (Phase 2, P2-05).

This is **not** semantic/embedding search. It searches stored symbols and the
current source text of files in the latest completed run:

- ``symbol`` / ``identifier``: symbol names and qualified names.
- ``text``: case-insensitive substring over source lines.
- ``token``: whole-word token matching over source lines (all tokens required).
- ``all``: symbol matching plus substring text matching.

Ranking is a documented integer score. Ties break on repository-relative path,
line, hit kind, and qualified name, so pages are stable across requests. The
caller supplies already-read source text; this module performs no I/O.
"""

from __future__ import annotations

import re
from collections import defaultdict

from repolens.core.models import (
    FileRecord,
    SearchHit,
    SearchHitKind,
    SearchMode,
    SymbolKind,
    SymbolRecord,
)

SCORE_EXACT_NAME = 100
SCORE_NAME_PREFIX = 90
SCORE_NAME_SUBSTRING = 80
SCORE_QUALIFIED_EXACT = 75
SCORE_QUALIFIED_SUFFIX = 70
SCORE_QUALIFIED_SUBSTRING = 60
SCORE_ALL_TOKENS = 50
SCORE_TEXT_EXACT_LINE = 50
SCORE_TEXT_WHOLE_WORD = 45
SCORE_TEXT_SUBSTRING = 40

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_WORD_RE = re.compile(r"\w+")
_DEFINITION_LINE_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([^\W\d]\w*)")


def _tokens(query: str) -> list[str]:
    return [token.lower() for token in query.split() if token]


def _path_matches(rel_path: str, path_prefix: str | None) -> bool:
    if not path_prefix:
        return True
    prefix = path_prefix.strip().strip("/")
    if not prefix:
        return True
    return rel_path == prefix or rel_path.startswith(prefix + "/")


def _symbol_score(symbol: SymbolRecord, query: str) -> tuple[int, str] | None:
    q = query.strip().lower()
    if not q:
        return None
    name = symbol.name.lower()
    qualified = symbol.qualified_name.lower()
    tokens = _tokens(query)

    if name == q:
        return SCORE_EXACT_NAME, "exact symbol name"
    if name.startswith(q):
        return SCORE_NAME_PREFIX, "symbol name prefix"
    if q in name:
        return SCORE_NAME_SUBSTRING, "symbol name substring"
    if qualified == q:
        return SCORE_QUALIFIED_EXACT, "exact qualified name"
    if qualified.endswith("." + q):
        return SCORE_QUALIFIED_SUFFIX, "qualified name suffix"
    if q in qualified:
        return SCORE_QUALIFIED_SUBSTRING, "qualified name substring"
    if tokens and all(token in name or token in qualified for token in tokens):
        return SCORE_ALL_TOKENS + len(tokens), "all tokens in symbol name"
    return None


def _rank_key(hit: SearchHit) -> tuple[object, ...]:
    return (
        -hit.score,
        hit.rel_path,
        hit.line if hit.line is not None else 0,
        hit.kind.value,
        hit.qualified_name or "",
        hit.snippet or "",
    )


def search_symbols(
    query: str,
    symbols: list[SymbolRecord],
    *,
    kind: SymbolKind | None = None,
    path_prefix: str | None = None,
) -> list[SearchHit]:
    """Rank symbol hits for a query. Results are not paginated here."""
    hits: list[SearchHit] = []
    for symbol in symbols:
        if kind is not None and symbol.kind is not kind:
            continue
        if not _path_matches(symbol.rel_path, path_prefix):
            continue
        scored = _symbol_score(symbol, query)
        if scored is None:
            continue
        score, reason = scored
        hits.append(
            SearchHit(
                kind=SearchHitKind.SYMBOL,
                score=score,
                rel_path=symbol.rel_path,
                line=symbol.start_line,
                end_line=symbol.end_line,
                snippet=symbol.qualified_name,
                matched_text=symbol.name,
                symbol_id=symbol.id,
                qualified_name=symbol.qualified_name,
                symbol_kind=symbol.kind,
                match_reason=reason,
            )
        )
    hits.sort(key=_rank_key)
    return hits


def _enclosing_symbol(
    symbols_by_path: dict[str, list[SymbolRecord]], rel_path: str, line: int
) -> SymbolRecord | None:
    candidates = [
        symbol
        for symbol in symbols_by_path.get(rel_path, [])
        if symbol.start_line <= line <= symbol.end_line
    ]
    if not candidates:
        return None
    by_id = {symbol.id: symbol for symbol in symbols_by_path.get(rel_path, [])}

    def depth(symbol: SymbolRecord) -> int:
        seen: set[str] = set()
        current = symbol
        value = 0
        while current.parent_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            parent = by_id.get(current.parent_id)
            if parent is None:
                break
            value += 1
            current = parent
        return value

    candidates.sort(
        key=lambda symbol: (
            symbol.end_line - symbol.start_line,
            -depth(symbol),
            -symbol.start_line,
            symbol.qualified_name,
        )
    )
    return candidates[0]


def _match_text_line(
    line: str,
    query: str,
    mode: SearchMode,
) -> tuple[int, str, str] | None:
    lowered = line.lower()
    q = query.lower()
    if mode is SearchMode.TOKEN:
        tokens = _tokens(query)
        if not tokens:
            return None
        words = {word.lower() for word in _WORD_RE.findall(line)}
        if not all(token in words for token in tokens):
            return None
        return SCORE_TEXT_WHOLE_WORD, query, "all whole-word tokens present"
    if q not in lowered:
        return None
    if line.strip().lower() == q:
        return SCORE_TEXT_EXACT_LINE, line.strip(), "exact line"
    # Prefer a whole-word occurrence for the reason text, but keep substring
    # matching as the semantic (it is the documented text mode).
    whole = re.search(rf"\b{re.escape(query)}\b", line, flags=re.IGNORECASE)
    if whole:
        return SCORE_TEXT_WHOLE_WORD, whole.group(0), "whole-word substring"
    index = lowered.index(q)
    return SCORE_TEXT_SUBSTRING, line[index : index + len(query)], "line substring"


def search_text(
    query: str,
    files: list[tuple[FileRecord, str]],
    symbols: list[SymbolRecord],
    *,
    mode: SearchMode = SearchMode.TEXT,
    path_prefix: str | None = None,
    kind: SymbolKind | None = None,
    max_per_file: int = 50,
    max_hits: int = 1_000,
) -> tuple[list[SearchHit], bool]:
    """Search current source lines. Returns hits and a truncation flag."""
    symbols_by_path: dict[str, list[SymbolRecord]] = defaultdict(list)
    for symbol in symbols:
        symbols_by_path[symbol.rel_path].append(symbol)
    hits: list[SearchHit] = []
    truncated = False
    for file, content in files:
        if not _path_matches(file.rel_path, path_prefix):
            continue
        per_file = 0
        for line_number, line in enumerate(content.splitlines(), start=1):
            matched = _match_text_line(line, query, mode)
            if matched is None:
                continue
            enclosing = _enclosing_symbol(symbols_by_path, file.rel_path, line_number)
            if kind is not None and (enclosing is None or enclosing.kind is not kind):
                continue
            if per_file >= max_per_file:
                truncated = True
                break
            if len(hits) >= max_hits:
                truncated = True
                break
            score, matched_text, reason = matched
            snippet = line.strip()
            if len(snippet) > 300:
                snippet = snippet[:300] + "…"
            hits.append(
                SearchHit(
                    kind=SearchHitKind.TEXT,
                    score=score,
                    rel_path=file.rel_path,
                    line=line_number,
                    end_line=line_number,
                    snippet=snippet,
                    matched_text=matched_text,
                    symbol_id=enclosing.id if enclosing else None,
                    qualified_name=enclosing.qualified_name if enclosing else None,
                    symbol_kind=enclosing.kind if enclosing else None,
                    match_reason=reason,
                )
            )
            per_file += 1
        if len(hits) >= max_hits:
            truncated = True
            break
    return hits, truncated


def combine_hits(
    symbol_hits: list[SearchHit],
    text_hits: list[SearchHit],
    *,
    max_hits: int = 1_000,
) -> tuple[list[SearchHit], bool]:
    """Deduplicate definition-line text hits and rank deterministically."""
    symbol_positions = {(hit.rel_path, hit.line) for hit in symbol_hits if hit.line is not None}
    definition_hits = {
        (hit.symbol_id, hit.matched_text): hit for hit in symbol_hits if hit.symbol_id
    }

    def duplicate_definition_line(hit: SearchHit) -> bool:
        if (hit.rel_path, hit.line) in symbol_positions:
            return True
        match = _DEFINITION_LINE_RE.match(hit.snippet or "")
        if match is None or not hit.symbol_id:
            return False
        symbol_hit = definition_hits.get((hit.symbol_id, match.group(1)))
        return symbol_hit is not None

    filtered_text = [hit for hit in text_hits if not duplicate_definition_line(hit)]
    combined = [*symbol_hits, *filtered_text]
    combined.sort(key=_rank_key)
    truncated = len(combined) > max_hits
    return combined[:max_hits], truncated


def paginate(hits: list[SearchHit], *, limit: int, offset: int) -> tuple[list[SearchHit], int]:
    """Slice a ranked hit list; returns the page and the unpaginated total."""
    total = len(hits)
    return hits[offset : offset + limit], total


def validate_identifier_query(query: str) -> bool:
    return bool(_IDENTIFIER_RE.match(query.strip()))
