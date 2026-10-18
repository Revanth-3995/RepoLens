"""Module-name derivation and relative-import arithmetic.

A file's module name derives from its repository-relative POSIX path:
``pkg/mod.py -> pkg.mod``, ``pkg/__init__.py -> pkg``. A root-level
``__init__.py`` (no package directory) maps to ``__init__`` — an unusual
layout that is handled without crashing and documented in limitations.
"""

from __future__ import annotations


def module_name_for(rel_path: str) -> tuple[str, bool]:
    """Return ``(module_name, is_package)`` for a relative POSIX path."""
    parts = rel_path.split("/")
    filename = parts[-1]
    directories = parts[:-1]
    stem = filename[: -len(".py")] if filename.endswith(".py") else filename
    if stem == "__init__":
        if directories:
            return ".".join(directories), True
        return "__init__", True
    return ".".join([*directories, stem]), False


def package_of(module_name: str, is_package: bool) -> list[str]:
    """Package parts used as the base for level-1 relative imports."""
    parts = module_name.split(".") if module_name else []
    if is_package:
        return parts
    return parts[:-1]


def resolve_relative_module(
    importer_module: str, importer_is_package: bool, level: int, module: str
) -> str | None:
    """Resolve a relative import to an absolute dotted module name.

    Returns ``None`` when the import would escape the top-level package
    (invalid in Python as well).
    """
    if level <= 0:
        return module or None
    base = package_of(importer_module, importer_is_package)
    up = level - 1
    if up > len(base):
        return None
    target = base[: len(base) - up]
    if module:
        target = [*target, *module.split(".")]
    if not target:
        return None
    return ".".join(target)
