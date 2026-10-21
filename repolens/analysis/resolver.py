"""Conservative static resolution of imports, calls, and inheritance.

Two-phase design:

Phase 1 — indexing: module index (module name -> file), global symbol index
(qualified name -> symbol), per-scope import-binding tables, parameter and
local-name tables for shadowing detection, and the class inheritance map.

Phase 2 — resolution: every import statement, call expression, and class base
produces a :class:`RelationshipDraft` with an explicit resolution status:

- ``resolved_static``  concrete in-repository target + recorded evidence
- ``external``         standard library / builtins / not present in repository
- ``unresolved``       statically indeterminable (shadowing, unknown receiver,
                       missing relative target, ...)
- ``ambiguous``        wildcard import could provide the name
- ``dynamic_or_unknown`` indirect call forms and locally bound receivers

Uncertain targets are never upgraded to ``resolved_static``.
"""

from __future__ import annotations

import builtins
import re
import sys
from collections import Counter
from typing import Any

from repolens.analysis.modules import resolve_relative_module
from repolens.core.models import (
    CallForm,
    CallRecord,
    FileAnalysis,
    ImportRecord,
    RelationshipDraft,
    RelationshipType,
    ResolutionStatus,
    SymbolDraft,
    SymbolKind,
)

_STDLIB = set(sys.stdlib_module_names)
_BUILTIN_MODULES = set(sys.builtin_module_names)
_BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins))
_DOTTED = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")


class _Bind:
    """An import binding created in some scope."""

    __slots__ = ("kind", "module", "name", "relative")

    def __init__(self, kind: str, module: str | None, name: str | None, relative: bool) -> None:
        self.kind = kind  # "module" | "symbol"
        self.module = module
        self.name = name
        self.relative = relative


def _methods_of(file: FileAnalysis, class_qname: str) -> dict[str, SymbolDraft]:
    """Direct methods of a class, keyed by simple name."""
    prefix = class_qname + "."
    out: dict[str, SymbolDraft] = {}
    for s in file.symbols:
        if s.kind is SymbolKind.METHOD and s.qualified_name.startswith(prefix):
            rest = s.qualified_name[len(prefix) :]
            if "." not in rest:
                out.setdefault(s.name, s)
    return out


class Resolver:
    """Produces relationship drafts for one repository's analysis outcome."""

    def __init__(self, repository_id: str, files: list[FileAnalysis]) -> None:
        self.repository_id = repository_id
        self.files = [f for f in files if f.parse_status.value == "parsed"]
        self._file_by_rel = {f.rel_path: f for f in self.files}
        self._file_by_module: dict[str, FileAnalysis] = {}
        for f in self.files:
            self._file_by_module.setdefault(f.module_name, f)
        # qualified name -> symbol draft (first wins; module names make these
        # unique across files in practice — documented in limitations)
        self._sym_by_q: dict[str, SymbolDraft] = {}
        # symbol id -> file that contains it
        self._file_of_symbol: dict[str, FileAnalysis] = {}
        for f in self.files:
            for s in f.symbols:
                self._sym_by_q.setdefault(s.qualified_name, s)
                if s.symbol_id:
                    self._file_of_symbol.setdefault(s.symbol_id, f)
        # Qualified names that have more than one definition: the resolver
        # cannot statically determine which binding is active, so any
        # relationship that would resolve to such a name is reported as
        # ambiguous instead of resolved_static.
        q_counts = Counter(s.qualified_name for f in self.files for s in f.symbols)
        self._duplicate_qnames: set[str] = {q for q, n in q_counts.items() if n > 1}
        # (rel_path, scope_qualified) -> {name: _Bind}
        self._bindings: dict[tuple[str, str], dict[str, _Bind]] = {}
        # params per function scope: (rel_path, scope) -> set of names
        self._params: dict[tuple[str, str], set[str]] = {}
        self._inherit_map: dict[str, list[SymbolDraft]] = {}
        self._inherit_built = False
        self._index_bindings()

    # ----------------------------------------------------------------- index

    def _index_bindings(self) -> None:
        for f in self.files:
            for s in f.symbols:
                if s.parameters:
                    self._params[(f.rel_path, s.qualified_name)] = {p.name for p in s.parameters}
            for imp in f.imports:
                scope = imp.owner_qualified or f.module_name
                table = self._bindings.setdefault((f.rel_path, scope), {})
                for name, bind in self._bindings_for(f, imp):
                    table.setdefault(name, bind)

    def _target_module(self, f: FileAnalysis, imp: ImportRecord) -> tuple[str | None, bool]:
        """Return (absolute target module or None, is_relative)."""
        if imp.level > 0:
            target = resolve_relative_module(
                f.module_name, f.rel_path.endswith("__init__.py"), imp.level, imp.module
            )
            return target, True
        return (imp.module or None), False

    def _bindings_for(self, f: FileAnalysis, imp: ImportRecord) -> list[tuple[str, _Bind]]:
        target, relative = self._target_module(f, imp)
        out: list[tuple[str, _Bind]] = []
        if imp.kind == "import":
            if imp.module:
                alias = imp.alias or imp.module.split(".")[0]
                out.append((alias, _Bind("module", imp.module, None, False)))
            return out
        if imp.star or target is None:
            return out
        for name, asname in imp.names:
            alias_name = asname or name
            out.append((alias_name, _Bind("symbol", target or "", name, relative)))
        return out

    # ----------------------------------------------------------- scope walk

    @staticmethod
    def _scope_chain(f: FileAnalysis, owner: str | None) -> list[str]:
        """Innermost-first chain of enclosing scopes (owner ... module)."""
        owner = owner or f.module_name
        chain = [owner]
        cur = owner
        module_dots = f.module_name.count(".")
        while cur.count(".") > module_dots:
            cur = cur.rsplit(".", 1)[0]
            chain.append(cur)
        return chain

    def _lookup_name(
        self, f: FileAnalysis, chain: list[str], name: str
    ) -> tuple[str, str | _Bind | None]:
        """Walk scopes innermost-first.

        Returns one of:
        - ("binding", _Bind)          name is import-bound in that scope
        - ("param", scope)            name is a parameter in that scope
        - ("local_def", scope)        name is a stable nested def/class
        - ("local", scope)            name is otherwise bound locally
        - ("miss", None)              not found in any scope
        """
        for scope in chain:
            bind = self._bindings.get((f.rel_path, scope), {}).get(name)
            if bind is not None:
                return "binding", bind
            if name in self._params.get((f.rel_path, scope), set()):
                return "param", scope
            if name in f.stable_local_defs.get(scope, set()):
                return "local_def", scope
            if name in f.local_names.get(scope, set()):
                return "local", scope
        return "miss", None

    # ------------------------------------------------------- chain walkers

    def _walk_module_chain(self, start_module: str, attrs: tuple[str, ...]) -> SymbolDraft | None:
        """Resolve ``start_module.attr1.attr2...`` to a symbol (or None)."""
        cur_module = start_module
        idx = 0
        f = self._file_by_module.get(cur_module)
        if f is None:
            # consume dotted prefixes that are themselves known modules
            # (e.g. binding root 'pkg' for 'import pkg.sub' with no pkg/__init__)
            while idx < len(attrs):
                candidate = f"{cur_module}.{attrs[idx]}"
                if candidate in self._file_by_module:
                    cur_module = candidate
                    idx += 1
                    f = self._file_by_module[cur_module]
                    break
                return None
            if f is None:
                return None
        for i in range(idx, len(attrs)):
            attr = attrs[i]
            sym = self._sym_by_q.get(f"{cur_module}.{attr}")
            if sym is None:
                return None
            if i == len(attrs) - 1:
                return sym if sym.kind is not SymbolKind.MODULE else None
            if sym.kind is SymbolKind.MODULE:
                nxt = self._file_by_module.get(sym.qualified_name)
                if nxt is None:
                    return None
                f = nxt
                cur_module = sym.qualified_name
                continue
            if sym.kind is SymbolKind.CLASS:
                if i + 1 == len(attrs) - 1:
                    return _methods_of(f, sym.qualified_name).get(attrs[i + 1])
                nested = self._sym_by_q.get(f"{sym.qualified_name}.{attrs[i + 1]}")
                if nested is not None and nested.kind is SymbolKind.CLASS:
                    # continue class walk in the same file
                    for j in range(i + 2, len(attrs)):
                        q = f"{nested.qualified_name}.{attrs[j]}"
                        cand = self._sym_by_q.get(q)
                        if cand is None:
                            return None
                        if j == len(attrs) - 1:
                            return cand if cand.kind is not SymbolKind.MODULE else None
                        if cand.kind is not SymbolKind.CLASS:
                            return None
                        nested = cand
                    return None
                return None
            return None
        return None

    def _find_method(
        self, class_draft: SymbolDraft, name: str, seen: set[str] | None = None
    ) -> SymbolDraft | None:
        """Method lookup on a class, following statically resolved bases."""
        seen = seen or set()
        if class_draft.qualified_name in seen:
            return None
        seen.add(class_draft.qualified_name)
        f = self._file_of_symbol.get(class_draft.symbol_id)
        if f is None:
            return None
        m = _methods_of(f, class_draft.qualified_name).get(name)
        if m is not None:
            return m
        for base in self._inherit_map.get(class_draft.qualified_name, []):
            m = self._find_method(base, name, seen)
            if m is not None:
                return m
        return None

    def _enclosing_class(self, f: FileAnalysis, chain: list[str]) -> SymbolDraft | None:
        for scope in chain:
            sym = self._sym_by_q.get(scope)
            if sym is not None and sym.kind is SymbolKind.CLASS:
                return sym
        return None

    # ------------------------------------------------------------ inheritance

    def _ensure_inherit_map(self) -> None:
        if self._inherit_built:
            return
        self._inherit_built = True
        for f in self.files:
            for s in f.symbols:
                if s.kind is not SymbolKind.CLASS or not s.bases:
                    continue
                targets: list[SymbolDraft] = []
                for base_text in s.bases:
                    target, _status, _ev = self._resolve_base(f, s, base_text)
                    if target is not None:
                        targets.append(target)
                self._inherit_map.setdefault(s.qualified_name, []).extend(targets)

    def _resolve_base(
        self, f: FileAnalysis, cls: SymbolDraft, base_text: str
    ) -> tuple[SymbolDraft | None, ResolutionStatus, str]:
        """Resolve a class base expression conservatively."""
        if not _DOTTED.match(base_text):
            return None, ResolutionStatus.UNRESOLVED, f"complex base expression: {base_text}"
        parts = base_text.split(".")
        root, attrs = parts[0], tuple(parts[1:])
        chain = self._scope_chain(f, cls.parent_qualified)
        kind, value = self._lookup_name(f, chain, root)
        if kind == "binding" and isinstance(value, _Bind):
            bind = value
            if bind.kind == "module":
                sym = self._walk_module_chain(bind.module or "", attrs)
                if sym is not None and sym.kind is SymbolKind.CLASS:
                    return (
                        sym,
                        ResolutionStatus.RESOLVED_STATIC,
                        f"base {base_text} -> {sym.qualified_name}",
                    )
                return (
                    None,
                    self._missing_module_status(bind),
                    f"base module {bind.module} not found in repository",
                )
            # symbol binding
            target_file = self._file_by_module.get(bind.module or "")
            if target_file is None:
                return (
                    None,
                    self._missing_module_status(bind),
                    f"imported module {bind.module} not found",
                )
            sym = self._sym_by_q.get(f"{bind.module}.{bind.name}")
            if sym is None:
                return (
                    None,
                    ResolutionStatus.UNRESOLVED,
                    f"imported name {bind.module}.{bind.name} not among extracted symbols",
                )
            if attrs:
                if sym.kind is SymbolKind.MODULE:
                    deeper = self._walk_module_chain(sym.qualified_name, attrs)
                    if deeper is not None and deeper.kind is SymbolKind.CLASS:
                        return (
                            deeper,
                            ResolutionStatus.RESOLVED_STATIC,
                            f"base {base_text} -> {deeper.qualified_name}",
                        )
                    return None, ResolutionStatus.UNRESOLVED, f"base chain {base_text} not found"
                if sym.kind is SymbolKind.CLASS:
                    q = f"{sym.qualified_name}.{'.'.join(attrs)}"
                    deeper = self._sym_by_q.get(q)
                    if deeper is not None and deeper.kind is SymbolKind.CLASS:
                        return deeper, ResolutionStatus.RESOLVED_STATIC, f"base {base_text} -> {q}"
                    return None, ResolutionStatus.UNRESOLVED, f"base chain {base_text} not found"
                return (
                    None,
                    ResolutionStatus.UNRESOLVED,
                    f"base {base_text}: imported symbol is not a class/module",
                )
            if sym.kind is SymbolKind.CLASS:
                return (
                    sym,
                    ResolutionStatus.RESOLVED_STATIC,
                    f"base {base_text} -> {sym.qualified_name}",
                )
            return None, ResolutionStatus.UNRESOLVED, f"imported name {base_text} is not a class"
        if kind in ("param", "local"):
            return (
                None,
                ResolutionStatus.UNRESOLVED,
                f"base {base_text} bound locally; type unknown",
            )
        # module-level definition in same file
        sym = self._sym_by_q.get(f"{f.module_name}.{root}")
        if sym is not None and sym.kind is SymbolKind.CLASS and not attrs:
            return (
                sym,
                ResolutionStatus.RESOLVED_STATIC,
                f"base {base_text} -> {sym.qualified_name}",
            )
        if sym is not None and sym.kind is SymbolKind.CLASS and attrs:
            q = f"{sym.qualified_name}.{'.'.join(attrs)}"
            deeper = self._sym_by_q.get(q)
            if deeper is not None and deeper.kind is SymbolKind.CLASS:
                return deeper, ResolutionStatus.RESOLVED_STATIC, f"base {base_text} -> {q}"
            return None, ResolutionStatus.UNRESOLVED, f"base chain {base_text} not found"
        if root in _BUILTIN_NAMES:
            return None, ResolutionStatus.EXTERNAL, f"builtin base class: {root}"
        if f.star_import:
            return (
                None,
                ResolutionStatus.AMBIGUOUS,
                f"wildcard import present; base {base_text} ambiguous",
            )
        return None, ResolutionStatus.UNRESOLVED, f"base {base_text} not found in repository"

    def _missing_module_status(self, bind: _Bind) -> ResolutionStatus:
        if bind.relative:
            return ResolutionStatus.UNRESOLVED
        return ResolutionStatus.EXTERNAL

    # ------------------------------------------------------------- imports

    def resolve_imports(self) -> list[RelationshipDraft]:
        rels: list[RelationshipDraft] = []
        for f in self.files:
            for imp in f.imports:
                rels.extend(self._import_rels(f, imp))
        return rels

    def _import_rels(self, f: FileAnalysis, imp: ImportRecord) -> list[RelationshipDraft]:
        target, relative = self._target_module(f, imp)
        owner_id = self._owner_symbol_id(f, imp.owner_qualified)
        base: dict[str, Any] = {
            "rel_type": RelationshipType.IMPORTS,
            "source_file_id": f.file_id,
            "source_symbol_id": owner_id,
            "source_rel_path": f.rel_path,
            "source_line": int(imp.line) if imp.line is not None else 0,
            "source_col": int(imp.col) if imp.col is not None else 0,
            "call_expr": None,
        }

        def resolved(ref: str, target_symbol_id: str | None, evidence: str) -> RelationshipDraft:
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.RESOLVED_STATIC,
                target_ref=ref,
                target_symbol_id=target_symbol_id,
                evidence=evidence,
            )

        def external(ref: str, evidence: str) -> RelationshipDraft:
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.EXTERNAL,
                target_ref=ref,
                target_symbol_id=None,
                evidence=evidence,
            )

        def unresolved(ref: str, evidence: str) -> RelationshipDraft:
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.UNRESOLVED,
                target_ref=ref,
                target_symbol_id=None,
                evidence=evidence,
            )

        def ambiguous(ref: str, evidence: str) -> RelationshipDraft:
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.AMBIGUOUS,
                target_ref=ref,
                target_symbol_id=None,
                evidence=evidence,
            )

        if imp.kind == "import":
            mod = imp.module
            ref = f"{mod} as {imp.alias}" if imp.alias else mod
            if target is None:
                return [unresolved(ref, "malformed module reference")]
            tf = self._file_by_module.get(target)
            if tf is not None:
                return [
                    resolved(
                        ref,
                        self._module_symbol_id(tf),
                        f"internal module {target} -> {tf.rel_path}",
                    )
                ]
            head = target.split(".")[0]
            if head in _STDLIB or head in _BUILTIN_MODULES:
                return [external(ref, f"standard library module: {target}")]
            return [
                external(ref, f"not present in repository; third-party or unavailable: {target}")
            ]

        # from-imports
        out: list[RelationshipDraft] = []
        if target is None:
            raw = f"from {'.' * imp.level}{imp.module} import ..."
            out.append(unresolved(raw, "relative import escapes top-level package"))
            return out
        tf = self._file_by_module.get(target)
        if imp.star:
            ref = f"from {target} import *"
            if tf is not None:
                out.append(
                    resolved(
                        ref,
                        self._module_symbol_id(tf),
                        f"wildcard import from internal module {target}",
                    )
                )
            else:
                out.append(external(ref, f"wildcard import from unavailable module {target}"))
            return out
        for name, alias in imp.names:
            ref = f"{target}.{name}" + (f" as {alias}" if alias else "")
            if tf is None:
                head = target.split(".")[0]
                if head in _STDLIB or head in _BUILTIN_MODULES:
                    out.append(external(ref, f"standard library module: {target}"))
                else:
                    out.append(
                        external(
                            ref, f"not present in repository; third-party or unavailable: {target}"
                        )
                    )
                continue
            sym = self._sym_by_q.get(f"{target}.{name}")
            if sym is not None:
                if sym.qualified_name in self._duplicate_qnames:
                    out.append(
                        ambiguous(
                            ref,
                            f"imported name {target}.{name} has multiple definitions; "
                            "static binding indeterminate",
                        )
                    )
                else:
                    out.append(resolved(ref, sym.symbol_id, f"internal symbol {target}.{name}"))
            else:
                # module itself resolved; the name may be a variable/re-export
                out.append(
                    resolved(
                        ref,
                        self._module_symbol_id(tf),
                        f"internal module {target} resolved; name '{name}' not among extracted symbols"
                        " (variable or re-export)",
                    )
                )
        return out

    # ---------------------------------------------------------------- calls

    def resolve_calls(self) -> list[RelationshipDraft]:
        self._ensure_inherit_map()
        rels: list[RelationshipDraft] = []
        for f in self.files:
            for call in f.calls:
                rels.append(self._call_rel(f, call))
        return rels

    def _base(self, f: FileAnalysis, call: CallRecord) -> dict[str, Any]:
        return {
            "rel_type": RelationshipType.CALLS,
            "source_file_id": f.file_id,
            "source_symbol_id": self._owner_symbol_id(f, call.owner_qualified),
            "source_rel_path": f.rel_path,
            "source_line": int(call.line) if call.line is not None else 0,
            "source_col": int(call.col) if call.col is not None else 0,
            "call_expr": call.func_repr,
        }

    def _call_rel(self, f: FileAnalysis, call: CallRecord) -> RelationshipDraft:
        base = self._base(f, call)
        if call.form is CallForm.INDIRECT:
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.DYNAMIC_OR_UNKNOWN,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"indirect call expression: {call.func_repr}",
            )
        if call.root_name is None:
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.DYNAMIC_OR_UNKNOWN,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence="call target expression could not be decomposed",
            )
        chain = self._scope_chain(f, call.owner_qualified)

        # --- self/cls method dispatch ------------------------------------
        if call.root_name in ("self", "cls") and call.attrs and len(call.attrs) == 1:
            cls = self._enclosing_class(f, chain)
            if cls is not None:
                m = self._find_method(cls, call.attrs[0])
                if m is not None:
                    return self._resolved(
                        base,
                        call,
                        m,
                        f"method lookup on {call.root_name} in class {cls.qualified_name}",
                    )
                return RelationshipDraft(
                    **base,
                    resolution_status=ResolutionStatus.UNRESOLVED,
                    target_ref=call.func_repr,
                    target_symbol_id=None,
                    evidence=f"method {call.attrs[0]} not found on class {cls.qualified_name}"
                    " or its statically resolved bases",
                )

        kind, value = self._lookup_name(f, chain, call.root_name)

        if kind == "binding" and isinstance(value, _Bind):
            return self._call_via_binding(f, call, base, value)

        if kind == "param":
            if call.form is CallForm.NAME:
                return RelationshipDraft(
                    **base,
                    resolution_status=ResolutionStatus.UNRESOLVED,
                    target_ref=call.func_repr,
                    target_symbol_id=None,
                    evidence=f"'{call.root_name}' is a parameter of {value}; runtime target unknown",
                )
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.DYNAMIC_OR_UNKNOWN,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"receiver '{call.root_name}' is a parameter; type unknown",
            )

        if kind == "local_def":
            candidate = self._sym_by_q.get(f"{value}.{call.root_name}")
            if candidate is not None and call.form is CallForm.NAME:
                if candidate.qualified_name in self._duplicate_qnames:
                    return RelationshipDraft(
                        **base,
                        resolution_status=ResolutionStatus.AMBIGUOUS,
                        target_ref=call.func_repr,
                        target_symbol_id=None,
                        evidence=f"nested definition {candidate.qualified_name} is duplicated",
                    )
                if candidate.kind in (SymbolKind.FUNCTION, SymbolKind.CLASS):
                    return self._resolved(
                        base,
                        call,
                        candidate,
                        f"stable nested definition {candidate.qualified_name}",
                    )
            if (
                candidate is not None
                and candidate.kind is SymbolKind.CLASS
                and len(call.attrs) == 1
            ):
                method = self._find_method(candidate, call.attrs[0])
                if method is not None:
                    return self._resolved(
                        base,
                        call,
                        method,
                        f"method lookup on nested class {candidate.qualified_name}",
                    )
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.UNRESOLVED,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"nested definition '{call.root_name}' in {value} has no matching target",
            )

        if kind == "local":
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.DYNAMIC_OR_UNKNOWN,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"'{call.root_name}' bound locally in {value}; runtime target unknown",
            )

        # --- not shadowed: module-level definitions ----------------------
        if call.form is CallForm.NAME:
            name = call.root_name
            sym = self._sym_by_q.get(f"{f.module_name}.{name}")
            if sym is not None and sym.kind in (SymbolKind.FUNCTION, SymbolKind.CLASS):
                return self._resolved(
                    base, call, sym, f"module-level definition {sym.qualified_name}"
                )
            # binding-free external?
            if name in _BUILTIN_NAMES:
                return RelationshipDraft(
                    **base,
                    resolution_status=ResolutionStatus.EXTERNAL,
                    target_ref=f"builtin:{name}",
                    target_symbol_id=None,
                    evidence=f"builtin function or type: {name}",
                )
            if f.star_import:
                return RelationshipDraft(
                    **base,
                    resolution_status=ResolutionStatus.AMBIGUOUS,
                    target_ref=call.func_repr,
                    target_symbol_id=None,
                    evidence="wildcard import present in module; name may come from star import",
                )
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.UNRESOLVED,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"no static definition found for '{name}'",
            )

        # --- attribute call with unknown receiver ------------------------
        root = call.root_name
        cls = self._sym_by_q.get(f"{f.module_name}.{root}")
        if cls is not None and cls.kind is SymbolKind.CLASS and call.attrs:
            if len(call.attrs) == 1:
                m = self._find_method(cls, call.attrs[0])
                if m is not None:
                    return self._resolved(
                        base, call, m, f"method lookup on class {cls.qualified_name}"
                    )
                return RelationshipDraft(
                    **base,
                    resolution_status=ResolutionStatus.UNRESOLVED,
                    target_ref=call.func_repr,
                    target_symbol_id=None,
                    evidence=f"method {call.attrs[0]} not found on class {cls.qualified_name} or its bases",
                )
            deeper = self._walk_module_chain(f.module_name, (root, *call.attrs))
            if deeper is not None:
                return self._resolved(
                    base, call, deeper, f"attribute chain resolved via class {cls.qualified_name}"
                )
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.UNRESOLVED,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"attribute chain {'.'.join(call.attrs)} not found on {root}",
            )
        if cls is not None and cls.kind is SymbolKind.FUNCTION:
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.UNRESOLVED,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"attribute accessed on module-level function {cls.qualified_name}; receiver unknown",
            )
        if f.star_import:
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.AMBIGUOUS,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"wildcard import present; receiver '{root}' ambiguous",
            )
        return RelationshipDraft(
            **base,
            resolution_status=ResolutionStatus.UNRESOLVED,
            target_ref=call.func_repr,
            target_symbol_id=None,
            evidence=f"receiver '{root}' unknown; runtime type required for attribute dispatch",
        )

    def _call_via_binding(
        self, f: FileAnalysis, call: CallRecord, base: dict, bind: _Bind
    ) -> RelationshipDraft:
        if bind.kind == "module":
            if call.form is CallForm.NAME:
                return RelationshipDraft(
                    **base,
                    resolution_status=ResolutionStatus.UNRESOLVED,
                    target_ref=call.func_repr,
                    target_symbol_id=None,
                    evidence=f"'{call.root_name}' refers to module {bind.module}; calling a module"
                    " is not statically resolvable",
                )
            # attribute chain from a module binding
            sym = self._walk_module_chain(bind.module or "", call.attrs)
            if sym is not None:
                return self._resolved(base, call, sym, f"chain from imported module {bind.module}")
            head = (bind.module or "").split(".")[0]
            if bind.relative:
                return RelationshipDraft(
                    **base,
                    resolution_status=ResolutionStatus.UNRESOLVED,
                    target_ref=call.func_repr,
                    target_symbol_id=None,
                    evidence=f"relative import target {bind.module} not found in repository",
                )
            status = ResolutionStatus.EXTERNAL
            if head in _STDLIB or head in _BUILTIN_MODULES:
                ev = f"standard library call: {bind.module}.{'.'.join(call.attrs)}"
            else:
                ev = f"module {bind.module} not present in repository; third-party or unavailable"
            return RelationshipDraft(
                **base,
                resolution_status=status,
                target_ref=f"{bind.module}.{'.'.join(call.attrs)}",
                target_symbol_id=None,
                evidence=ev,
            )
        # symbol binding
        target_file = self._file_by_module.get(bind.module or "")
        if target_file is None:
            head = (bind.module or "").split(".")[0]
            status = ResolutionStatus.UNRESOLVED if bind.relative else ResolutionStatus.EXTERNAL
            ev = (
                f"relative import target {bind.module} not found in repository"
                if bind.relative
                else (
                    f"standard library module: {bind.module}"
                    if (head in _STDLIB or head in _BUILTIN_MODULES)
                    else f"module {bind.module} not present in repository; third-party or unavailable"
                )
            )
            return RelationshipDraft(
                **base,
                resolution_status=status,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=ev,
            )
        sym = self._sym_by_q.get(f"{bind.module}.{bind.name}")
        if sym is None:
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.UNRESOLVED,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"imported name {bind.module}.{bind.name} not among extracted symbols",
            )
        if call.form is CallForm.NAME:
            if sym.kind is SymbolKind.MODULE:
                return RelationshipDraft(
                    **base,
                    resolution_status=ResolutionStatus.UNRESOLVED,
                    target_ref=call.func_repr,
                    target_symbol_id=None,
                    evidence=f"'{call.root_name}' refers to module {bind.module}; calling a module"
                    " is not statically resolvable",
                )
            return self._resolved(
                base, call, sym, f"import binding {call.root_name} -> {sym.qualified_name}"
            )
        # attribute call: receiver is an imported symbol
        if sym.kind is SymbolKind.MODULE:
            deeper = self._walk_module_chain(sym.qualified_name, call.attrs)
            if deeper is not None:
                return self._resolved(
                    base, call, deeper, f"chain from imported module {sym.qualified_name}"
                )
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.EXTERNAL
                if not bind.relative
                else ResolutionStatus.UNRESOLVED,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"attribute chain not found in module {sym.qualified_name}",
            )
        if sym.kind is SymbolKind.CLASS and len(call.attrs) == 1:
            m = self._find_method(sym, call.attrs[0])
            if m is not None:
                return self._resolved(
                    base, call, m, f"method lookup on imported class {sym.qualified_name}"
                )
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.UNRESOLVED,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=f"method {call.attrs[0]} not found on imported class {sym.qualified_name} or its bases",
            )
        return RelationshipDraft(
            **base,
            resolution_status=ResolutionStatus.UNRESOLVED,
            target_ref=call.func_repr,
            target_symbol_id=None,
            evidence=f"receiver '{call.root_name}' is imported non-module symbol {sym.qualified_name};"
            " attribute dispatch requires runtime type",
        )

    # ----------------------------------------------------------- inheritance

    def resolve_inherits(self) -> list[RelationshipDraft]:
        self._ensure_inherit_map()
        rels: list[RelationshipDraft] = []
        for f in self.files:
            for s in f.symbols:
                if s.kind is not SymbolKind.CLASS or not s.bases:
                    continue
                for base_text in s.bases:
                    target, status, evidence = self._resolve_base(f, s, base_text)
                    if (
                        status is ResolutionStatus.RESOLVED_STATIC
                        and target is not None
                        and target.qualified_name in self._duplicate_qnames
                    ):
                        target = None
                        status = ResolutionStatus.AMBIGUOUS
                        evidence = (
                            f"base {base_text}: multiple definitions; static binding indeterminate"
                        )
                    rels.append(
                        RelationshipDraft(
                            rel_type=RelationshipType.INHERITS_FROM,
                            source_file_id=f.file_id,
                            source_symbol_id=s.symbol_id,
                            source_rel_path=f.rel_path,
                            source_line=s.start_line,
                            source_col=0,
                            resolution_status=status,
                            target_ref=base_text,
                            target_symbol_id=target.symbol_id if target else None,
                            call_expr=None,
                            evidence=evidence,
                        )
                    )
        return rels

    # -------------------------------------------------------------- helpers

    def _resolved(
        self, base: dict, call: CallRecord, target: SymbolDraft, evidence: str
    ) -> RelationshipDraft:
        if target.qualified_name in self._duplicate_qnames:
            return RelationshipDraft(
                **base,
                resolution_status=ResolutionStatus.AMBIGUOUS,
                target_ref=call.func_repr,
                target_symbol_id=None,
                evidence=(
                    f"multiple definitions named {target.qualified_name}; "
                    "static binding indeterminate"
                ),
            )
        return RelationshipDraft(
            **base,
            resolution_status=ResolutionStatus.RESOLVED_STATIC,
            target_ref=target.qualified_name,
            target_symbol_id=target.symbol_id,
            evidence=evidence,
        )

    @staticmethod
    def _module_symbol_id(f: FileAnalysis) -> str | None:
        for s in f.symbols:
            if s.kind is SymbolKind.MODULE:
                return s.symbol_id
        return None

    @staticmethod
    def _owner_symbol_id(f: FileAnalysis, owner_qualified: str | None) -> str | None:
        if owner_qualified is None:
            for s in f.symbols:
                if s.kind is SymbolKind.MODULE:
                    return s.symbol_id
            return None
        for s in f.symbols:
            if s.qualified_name == owner_qualified:
                return s.symbol_id
        for s in f.symbols:
            if s.kind is SymbolKind.MODULE:
                return s.symbol_id
        return None
