"""T21: the analyzer must never execute, import, or shell out to analyzed code.

This is a meta-test: it walks the ``repolens`` package's own AST and fails if a
dynamic-execution primitive appears.
"""

from __future__ import annotations

import ast
from pathlib import Path

import repolens

PACKAGE_ROOT = Path(repolens.__file__).parent

DANGEROUS_NAMES = {"exec", "eval", "compile", "__import__"}
DANGEROUS_ATTRS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "execv"),
    ("os", "execve"),
    ("importlib", "import_module"),
    ("pickle", "load"),
    ("pickle", "loads"),
}
DANGEROUS_MODULES = {"subprocess", "runpy", "pickle", "importlib", "ctypes"}


def _iter_python_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def test_package_has_files_to_scan():
    assert _iter_python_files()


def test_no_dynamic_execution_calls():
    offenders: list[str] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in DANGEROUS_NAMES:
                offenders.append(f"{path.name}:{node.lineno}: {func.id}()")
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                pair = (func.value.id, func.attr)
                if pair in DANGEROUS_ATTRS:
                    offenders.append(f"{path.name}:{node.lineno}: {func.value.id}.{func.attr}()")
    assert offenders == [], f"dynamic execution primitives found: {offenders}"


def test_no_dangerous_module_imports():
    offenders: list[str] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in DANGEROUS_MODULES:
                        offenders.append(f"{path.name}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                if root in DANGEROUS_MODULES:
                    offenders.append(f"{path.name}:{node.lineno}: from {node.module} import ...")
    assert offenders == [], f"dangerous imports found: {offenders}"


def test_analyzer_does_not_import_analyzed_fixtures():
    offenders: list[str] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            if any(m == "tests" or m.startswith("tests.") for m in modules):
                offenders.append(f"{path.name}:{node.lineno}: {modules}")
    assert offenders == [], f"analyzer imports test fixtures: {offenders}"
