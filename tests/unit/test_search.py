"""P2-05: deterministic ranking, filters, snippets and bounds for lexical search."""

from __future__ import annotations

from repolens.analysis.search import (
    combine_hits,
    paginate,
    search_symbols,
    search_text,
    validate_identifier_query,
)
from repolens.core.models import (
    FileRecord,
    ParseStatus,
    SearchHit,
    SearchHitKind,
    SearchMode,
    SymbolKind,
    SymbolRecord,
)


def _symbol(
    symbol_id: str,
    name: str,
    qualified_name: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    rel_path: str = "pkg/a.py",
    start_line: int = 1,
    end_line: int = 5,
) -> SymbolRecord:
    return SymbolRecord(
        id=symbol_id,
        repository_id="repo_1",
        file_id="file_1",
        rel_path=rel_path,
        name=name,
        qualified_name=qualified_name,
        kind=kind,
        start_line=start_line,
        end_line=end_line,
    )


def _file(rel_path: str = "pkg/a.py") -> FileRecord:
    return FileRecord(
        id="file_1",
        repository_id="repo_1",
        rel_path=rel_path,
        module_name=rel_path.removesuffix(".py").replace("/", "."),
        size_bytes=10,
        content_hash="hash",
        parse_status=ParseStatus.PARSED,
    )


def test_symbol_ranking_is_documented_and_ordered():
    symbols = [
        _symbol("s1", "foo", "foo"),
        _symbol("s2", "foo_bar", "foo_bar"),
        _symbol("s3", "inner", "outer.foo", kind=SymbolKind.METHOD),
        _symbol("s4", "other", "other"),
    ]
    hits = search_symbols("foo", symbols)
    assert [(hit.qualified_name, hit.score, hit.match_reason) for hit in hits] == [
        ("foo", 100, "exact symbol name"),
        ("foo_bar", 90, "symbol name prefix"),
        ("outer.foo", 70, "qualified name suffix"),
    ]


def test_symbol_ranking_breaks_ties_by_path_then_line_then_name():
    symbols = [
        _symbol("s2", "dup", "dup", rel_path="pkg/b.py", start_line=9),
        _symbol("s1", "dup", "dup", rel_path="pkg/a.py", start_line=9),
        _symbol("s3", "dup", "dup", rel_path="pkg/a.py", start_line=2),
    ]
    hits = search_symbols("dup", symbols)
    assert [(hit.rel_path, hit.line) for hit in hits] == [
        ("pkg/a.py", 2),
        ("pkg/a.py", 9),
        ("pkg/b.py", 9),
    ]


def test_all_tokens_match_when_query_is_not_contiguous():
    symbols = [_symbol("s1", "inner", "outer.foo_bar")]
    hits = search_symbols("foo bar", symbols)
    assert len(hits) == 1
    assert hits[0].score == 52
    assert hits[0].match_reason == "all tokens in symbol name"


def test_symbol_kind_and_path_filters():
    symbols = [
        _symbol("s1", "foo", "foo", kind=SymbolKind.FUNCTION, rel_path="pkg/a.py"),
        _symbol("s2", "Foo", "Foo", kind=SymbolKind.CLASS, rel_path="pkg/a.py"),
        _symbol("s3", "foo", "tests.foo", kind=SymbolKind.FUNCTION, rel_path="tests/a.py"),
    ]
    functions = search_symbols("foo", symbols, kind=SymbolKind.FUNCTION)
    assert {hit.symbol_id for hit in functions} == {"s1", "s3"}
    under_pkg = search_symbols("foo", symbols, path_prefix="pkg")
    assert {hit.symbol_id for hit in under_pkg} == {"s1", "s2"}
    exact_prefix = search_symbols("foo", symbols, path_prefix="pkg/a.py")
    assert {hit.symbol_id for hit in exact_prefix} == {"s1", "s2"}
    assert search_symbols("foo", symbols, path_prefix="pkgx") == []


def test_text_search_returns_lines_snippets_and_enclosing_symbol():
    content = "def alpha():\n    needle = 1\n    return needle\n"
    symbols = [_symbol("s1", "alpha", "a.alpha", start_line=1, end_line=3)]
    hits, truncated = search_text("needle", [(_file(), content)], symbols)
    assert truncated is False
    assert [(hit.line, hit.score, hit.snippet) for hit in hits] == [
        (2, 45, "needle = 1"),
        (3, 45, "return needle"),
    ]
    assert all(hit.symbol_id == "s1" for hit in hits)
    assert all(hit.qualified_name == "a.alpha" for hit in hits)
    assert all(hit.kind is SearchHitKind.TEXT for hit in hits)


def test_text_search_is_case_insensitive_and_substring_based():
    content = "alpha beta\nbeta alpha\nalphabet soup\n"
    hits, _ = search_text("ALPHA", [(_file(), content)], [], mode=SearchMode.TEXT)
    assert [hit.line for hit in hits] == [1, 2, 3]
    assert hits[2].matched_text == "alpha"


def test_token_mode_requires_all_whole_words():
    content = "alpha beta\nbeta alpha\nalphabet soup\n"
    hits, _ = search_text("alpha beta", [(_file(), content)], [], mode=SearchMode.TOKEN)
    assert [hit.line for hit in hits] == [1, 2]
    assert all(hit.match_reason == "all whole-word tokens present" for hit in hits)


def test_text_search_honours_path_prefix():
    content = "needle\n"
    files = [(_file("pkg/a.py"), content), (_file("tests/b.py"), content)]
    hits, _ = search_text("needle", files, [], path_prefix="pkg")
    assert [hit.rel_path for hit in hits] == ["pkg/a.py"]


def test_text_search_bounds_per_file_and_total():
    content = "needle\n" * 3
    hits, truncated = search_text("needle", [(_file(), content)], [], max_per_file=1)
    assert len(hits) == 1
    assert truncated is True
    hits, truncated = search_text("needle", [(_file(), content)], [], max_hits=2)
    assert len(hits) == 2
    assert truncated is True


def test_combine_deduplicates_a_definition_line_text_hit():
    symbol_hit = SearchHit(
        kind=SearchHitKind.SYMBOL,
        score=100,
        rel_path="pkg/a.py",
        line=1,
        end_line=3,
        snippet="foo",
        matched_text="foo",
        symbol_id="s1",
        qualified_name="foo",
        symbol_kind=SymbolKind.FUNCTION,
        match_reason="exact symbol name",
    )
    line_one = SearchHit(
        kind=SearchHitKind.TEXT,
        score=45,
        rel_path="pkg/a.py",
        line=1,
        snippet="def foo():",
        matched_text="foo",
        match_reason="whole-word substring",
    )
    line_two = SearchHit(
        kind=SearchHitKind.TEXT,
        score=45,
        rel_path="pkg/a.py",
        line=2,
        snippet="return foo",
        matched_text="foo",
        match_reason="whole-word substring",
    )
    combined, truncated = combine_hits([symbol_hit], [line_one, line_two])
    assert truncated is False
    assert [(hit.kind, hit.line) for hit in combined] == [
        (SearchHitKind.SYMBOL, 1),
        (SearchHitKind.TEXT, 2),
    ]


def test_combine_deduplicates_decorated_definition_line_text_hit():
    symbol_hit = SearchHit(
        kind=SearchHitKind.SYMBOL,
        score=100,
        rel_path="pkg/a.py",
        line=1,
        end_line=3,
        snippet="foo",
        matched_text="foo",
        symbol_id="s1",
        qualified_name="foo",
        symbol_kind=SymbolKind.FUNCTION,
        match_reason="exact symbol name",
    )
    definition_line = SearchHit(
        kind=SearchHitKind.TEXT,
        score=45,
        rel_path="pkg/a.py",
        line=2,
        snippet="def foo():",
        matched_text="foo",
        symbol_id="s1",
        qualified_name="foo",
        symbol_kind=SymbolKind.FUNCTION,
        match_reason="whole-word substring",
    )
    combined, truncated = combine_hits([symbol_hit], [definition_line])
    assert truncated is False
    assert [(hit.kind, hit.line) for hit in combined] == [(SearchHitKind.SYMBOL, 1)]


def test_paginate_returns_page_and_unpaginated_total():
    hits = [
        SearchHit(
            kind=SearchHitKind.SYMBOL,
            score=100 - index,
            rel_path="pkg/a.py",
            line=index + 1,
            snippet=f"hit{index}",
            matched_text="hit",
            match_reason="test",
        )
        for index in range(5)
    ]
    page, total = paginate(hits, limit=2, offset=1)
    assert total == 5
    assert [hit.line for hit in page] == [2, 3]


def test_no_results_is_an_empty_list():
    assert search_symbols("zzz", [_symbol("s1", "foo", "foo")]) == []
    hits, truncated = search_text("zzz", [(_file(), "alpha\n")], [])
    assert hits == []
    assert truncated is False


def test_identifier_query_validation():
    assert validate_identifier_query("foo") is True
    assert validate_identifier_query("pkg.foo") is True
    assert validate_identifier_query("foo bar") is False
    assert validate_identifier_query("1foo") is False
    assert validate_identifier_query("") is False
