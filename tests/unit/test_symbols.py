"""T8 / T9: symbol extraction, nesting, signatures, and collision-free identity."""

from __future__ import annotations

import ast

from repolens.analysis.pipeline import _assign_ids
from repolens.analysis.symbols import extract_symbols
from repolens.core.identifiers import symbol_id_for
from repolens.core.models import FileAnalysis, ParseStatus, SymbolKind

SOURCE = '''
@deco
@other(1)
class C(Base, pkg.Mixin):
    """Class doc."""
    attr: int = 0

    @staticmethod
    def m(self, a: int, b=2, *args, c, **kw) -> str:
        """Method doc."""
        return "x"


def f(a, /, b, *, c):
    return a


def outer():
    def inner():
        return 1
    return inner
'''


def _table(source: str = SOURCE):
    return extract_symbols(ast.parse(source), "mod", len(source.splitlines()))


def test_extracts_module_class_method_function():
    table = _table()
    kinds = {(d.qualified_name, d.kind) for d in table.drafts}
    assert ("mod", SymbolKind.MODULE) in kinds
    assert ("mod.C", SymbolKind.CLASS) in kinds
    assert ("mod.C.m", SymbolKind.METHOD) in kinds
    assert ("mod.f", SymbolKind.FUNCTION) in kinds


def test_class_metadata_bases_decorators_docstring():
    cls = _table().get("mod.C", SymbolKind.CLASS)
    assert cls is not None
    assert cls.bases == ["Base", "pkg.Mixin"]
    assert cls.decorators == ["deco", "other(1)"]
    assert cls.docstring == "Class doc."


def test_method_signature_parameters_and_return_annotation():
    method = _table().get("mod.C.m", SymbolKind.METHOD)
    assert method is not None
    assert method.docstring == "Method doc."
    assert method.return_annotation == "str"
    params = {p.name: p for p in method.parameters}
    assert params["a"].annotation == "int"
    assert params["b"].default == "2"
    assert params["args"].kind == "vararg"
    assert params["c"].kind == "kwonly"
    assert params["kw"].kind == "kwarg"


def test_positional_only_parameters():
    func = _table().get("mod.f", SymbolKind.FUNCTION)
    assert func is not None
    assert [p.kind for p in func.parameters] == ["posonly", "arg", "kwonly"]


def test_nested_function_has_parent_qualified():
    table = _table()
    inner = table.get("mod.outer.inner", SymbolKind.FUNCTION)
    assert inner is not None
    assert inner.parent_qualified == "mod.outer"


def test_line_ranges_include_decorators():
    cls = _table().get("mod.C", SymbolKind.CLASS)
    assert cls is not None
    # decorators are on lines 2 and 3 of SOURCE (after the leading newline)
    assert cls.start_line == 2
    assert cls.end_line >= cls.start_line


def test_duplicate_definitions_get_distinct_ordinals():
    source = "def f():\n    return 1\n\ndef f():\n    return 2\n"
    table = extract_symbols(ast.parse(source), "m", 5)
    fs = [d for d in table.drafts if d.qualified_name == "m.f"]
    assert [d.ordinal for d in fs] == [0, 1]


def test_redefinitions_produce_collision_free_ids():
    source = "def f():\n    pass\n\ndef f():\n    pass\n"
    table = extract_symbols(ast.parse(source), "m", 4)
    files = [
        FileAnalysis(
            rel_path="m.py",
            module_name="m",
            size_bytes=len(source),
            content_hash="h",
            parse_status=ParseStatus.PARSED,
            symbols=table.drafts,
        )
    ]
    _assign_ids("repo_x", files)
    ids = [s.symbol_id for s in files[0].symbols]
    assert len(ids) == len(set(ids))
    # Ids match the documented derivation.
    f0 = next(d for d in table.drafts if d.qualified_name == "m.f" and d.ordinal == 0)
    assert f0.symbol_id == symbol_id_for("repo_x", "m.py", "function", "m.f", 0)


def test_symbol_ids_stable_under_unrelated_edits():
    first = "def f():\n    pass\n\ndef g():\n    pass\n"
    second = "x = 1\n\ndef f():\n    pass\n\ndef g():\n    pass\n"
    t1 = extract_symbols(ast.parse(first), "m", 4)
    t2 = extract_symbols(ast.parse(second), "m", 6)
    f1 = next(d for d in t1.drafts if d.qualified_name == "m.f")
    f2 = next(d for d in t2.drafts if d.qualified_name == "m.f")
    assert f1.ordinal == f2.ordinal == 0
