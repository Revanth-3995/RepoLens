"""T10 / T11: import and call extraction across all forms."""

from __future__ import annotations

import ast

from repolens.analysis.imports_calls import extract_imports_and_calls
from repolens.analysis.symbols import extract_symbols
from repolens.core.models import CallForm

SOURCE = """
import os
import sys as system
from a.b import c as d, e
from . import sibling
from .pkg import thing
from math import *
import numpy.linalg


def run(self, obj):
    fn = object()
    return fn(self.method(), obj.attr(), getattr(obj, "x")())


def method(self):
    return 1
"""


def _extract(source: str = SOURCE):
    tree = ast.parse(source)
    table = extract_symbols(tree, "mod", len(source.splitlines()))
    return extract_imports_and_calls(tree, table.node_to_qualified, "mod")


def test_plain_and_aliased_imports():
    result = _extract()
    plain = [i for i in result.imports if i.kind == "import"]
    modules = {i.module for i in plain}
    assert "os" in modules
    assert "sys" in modules
    assert "numpy.linalg" in modules
    sys_import = next(i for i in plain if i.module == "sys")
    assert sys_import.alias == "system"


def test_from_imports_names_and_aliases():
    result = _extract()
    from_ab = next(i for i in result.imports if i.module == "a.b")
    assert from_ab.names == [("c", "d"), ("e", None)]


def test_relative_imports_capture_level():
    result = _extract()
    rel = [i for i in result.imports if i.level == 1]
    assert {i.module for i in rel} == {"", "pkg"}


def test_star_import_flagged():
    result = _extract()
    assert result.has_star_import is True
    assert any(i.star for i in result.imports)


def test_call_forms_and_owners():
    result = _extract()
    calls = result.calls
    object_call = next(c for c in calls if c.func_repr == "object")
    assert object_call.form is CallForm.NAME
    assert object_call.owner_qualified == "mod.run"

    attr_call = next(c for c in calls if c.func_repr == "self.method")
    assert attr_call.form is CallForm.ATTRIBUTE
    assert attr_call.root_name == "self"
    assert attr_call.attrs == ("method",)

    indirect = next(c for c in calls if c.form is CallForm.INDIRECT)
    assert indirect.func_repr == "getattr(obj, 'x')"


def test_local_names_collected_per_scope():
    result = _extract()
    assert "mod.run" in result.local_names
    assert "fn" in result.local_names["mod.run"]
