"""Per-function control-flow graphs (Phase 2, P2-02).

Semantics (also documented in ``docs/phase-2-plan.md``):

- One ``entry`` and one ``exit`` node per function.
- Simple statements become ``statement`` nodes; ``return``/``raise`` become
  terminal nodes connected to ``exit``; ``break``/``continue`` connect to the
  innermost enclosing loop.
- ``if``/``elif``/``else`` produce a ``condition`` node with ``true``/``false``
  edges and an explicit ``merge`` node for the join.
- ``for``/``while`` produce a ``loop`` header with ``loop_body``, ``loop_back``
  and ``loop_exit`` edges. ``break`` targets the loop-exit merge; ``continue``
  targets the loop header.
- ``with`` produces a ``with`` node and a join after the body.
- ``match`` produces a ``match`` node, one ``case`` node per case, and a
  ``no_match`` edge when the cases are not exhaustive.
- ``try``/``try*`` use conservative exception edges: every node created inside
  the try body gets an ``exception`` edge to every handler. Exception types,
  implicit propagation and match specificity are **not** modelled. ``else``
  runs only after normal body completion; ``finally`` joins normal and handled
  paths.
- Nested function/class/lambda bodies are never descended into; they have their
  own graphs. A nested definition is represented by one ``nested_def`` node.

Known approximation (reported in ``notes``): ``return``/``break``/``continue``
inside a ``try`` do not route through ``finally``; the graph records the
function exit or loop target directly. This is a control-flow approximation,
not a claim of complete Python exception/finalization semantics.

Construction is deterministic: statements are visited in source order and
frontier sets are iterated in sorted node-id order. Node/edge counts are capped
so pathological inputs cannot allocate unbounded memory; a capped graph is
returned with ``truncated=True``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from repolens.core.models import (
    CFGEdge,
    CFGEdgeType,
    CFGNode,
    CFGNodeKind,
    FunctionCFG,
)

DEFAULT_MAX_NODES = 5_000
DEFAULT_MAX_EDGES = 20_000
DEFAULT_MAX_DEPTH = 200


@dataclass(frozen=True)
class DefinitionDraft:
    """A local binding occurrence found while building the CFG."""

    name: str
    kind: str
    line: int
    column: int


@dataclass(frozen=True)
class UseDraft:
    """A name read found while building the CFG."""

    name: str
    line: int
    column: int
    # True when the read is evaluated after the definitions on the same node
    # (e.g. a match-case guard, which can see pattern bindings).
    after_defs: bool = False


@dataclass
class CFGFacts:
    """Per-node definitions/uses used by the local data-flow layer."""

    node_defs: dict[str, list[DefinitionDraft]] = field(default_factory=dict)
    node_uses: dict[str, list[UseDraft]] = field(default_factory=dict)
    non_local: set[str] = field(default_factory=set)
    notes: set[str] = field(default_factory=set)


@dataclass
class CFGWithFacts:
    cfg: FunctionCFG
    facts: CFGFacts


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total for stdlib nodes
        return "?"


def _short(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _stmt_label(node: ast.stmt) -> str:
    try:
        return _short(ast.unparse(node))
    except Exception:  # pragma: no cover
        return type(node).__name__


def _source_for(
    source_lines: list[str] | None, start: int | None, end: int | None, limit: int = 500
) -> str | None:
    if not source_lines or start is None:
        return None
    end = end or start
    if start < 1 or start > len(source_lines):
        return None
    text = "\n".join(source_lines[start - 1 : min(end, len(source_lines))]).strip()
    return _short(text, limit) if text else None


@dataclass
class _LoopContext:
    header: str
    break_target: str
    continue_target: str


class _Builder:
    def __init__(
        self,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        source_lines: list[str] | None,
        *,
        max_nodes: int,
        max_edges: int,
        max_depth: int,
    ) -> None:
        self.func = func_node
        self.source_lines = source_lines
        self.max_nodes = max(2, max_nodes)
        self.max_edges = max(1, max_edges)
        self.max_depth = max(1, max_depth)
        self.nodes: list[CFGNode] = []
        self.edges: list[CFGEdge] = []
        self.facts = CFGFacts()
        self.truncated = False
        self._seq = 0
        self._edge_keys: set[tuple[str, str, CFGEdgeType]] = set()
        self._loops: list[_LoopContext] = []
        self.entry = ""
        self.exit = ""
        self._notes: set[str] = set()

    # ----------------------------------------------------------- primitives

    def _new_node(
        self,
        kind: CFGNodeKind,
        label: str,
        stmt: ast.AST | None = None,
        *,
        line: int | None = None,
        end_line: int | None = None,
        column: int | None = None,
    ) -> str:
        if len(self.nodes) >= self.max_nodes:
            self.truncated = True
            self._notes.add(f"CFG node cap {self.max_nodes} reached")
            return ""
        line = line if line is not None else getattr(stmt, "lineno", None)
        end_line = end_line if end_line is not None else getattr(stmt, "end_lineno", line)
        column = column if column is not None else getattr(stmt, "col_offset", None)
        nid = f"n{self._seq}"
        self._seq += 1
        self.nodes.append(
            CFGNode(
                id=nid,
                kind=kind,
                label=_short(label),
                line=line,
                end_line=end_line,
                column=column,
                source=_source_for(self.source_lines, line, end_line),
            )
        )
        return nid

    def _edge(
        self,
        source: str,
        target: str,
        edge_type: CFGEdgeType,
        *,
        line: int | None = None,
        evidence: str = "",
    ) -> None:
        if not source or not target:
            return
        key = (source, target, edge_type)
        if key in self._edge_keys:
            return
        if len(self.edges) >= self.max_edges:
            self.truncated = True
            self._notes.add(f"CFG edge cap {self.max_edges} reached")
            return
        self._edge_keys.add(key)
        self.edges.append(
            CFGEdge(source=source, target=target, type=edge_type, line=line, evidence=evidence)
        )

    def _connect(self, frontier: set[str], target: str, edge_type: CFGEdgeType, **kw: Any) -> None:
        for source in sorted(frontier):
            self._edge(source, target, edge_type, **kw)

    def _add_def(
        self, node_id: str, name: str, kind: str, node: ast.AST, *, column: int | None = None
    ) -> None:
        if not node_id or not name:
            return
        self.facts.node_defs.setdefault(node_id, []).append(
            DefinitionDraft(
                name=name,
                kind=kind,
                line=int(getattr(node, "lineno", 0) or 0),
                column=int(column if column is not None else getattr(node, "col_offset", 0) or 0),
            )
        )

    def _add_use(self, node_id: str, name: str, node: ast.AST) -> None:
        if not node_id or not name:
            return
        self.facts.node_uses.setdefault(node_id, []).append(
            UseDraft(
                name=name,
                line=int(getattr(node, "lineno", 0) or 0),
                column=int(getattr(node, "col_offset", 0) or 0),
            )
        )

    # -------------------------------------------------------- fact extraction

    def _collect_uses(
        self, node_id: str, expr: ast.AST | None, *, after_defs: bool = False
    ) -> None:
        if expr is None or not node_id:
            return
        uses, walrus_targets = _expression_facts(expr)
        for name, lineno, col, after_walrus in uses:
            self.facts.node_uses.setdefault(node_id, []).append(
                UseDraft(
                    name=name,
                    line=lineno,
                    column=col,
                    after_defs=after_defs or after_walrus,
                )
            )
        # Walrus targets bind in the enclosing scope where the expression is
        # evaluated. They are definitions on the same node as their reads; uses
        # later in the same expression are marked so they see these definitions.
        for name, target in walrus_targets:
            self._add_def(node_id, name, "walrus", target)

    def _collect_targets(self, node_id: str, target: ast.AST, kind: str) -> None:
        if not node_id:
            return
        if isinstance(target, ast.Name):
            if isinstance(target.ctx, (ast.Store, ast.Del)):
                self._add_def(node_id, target.id, kind, target)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._collect_targets(node_id, element, kind)
            return
        if isinstance(target, ast.Starred):
            self._collect_targets(node_id, target.value, kind)
            return
        self._collect_uses(node_id, target)

    def _collect_simple(self, stmt: ast.stmt, node_id: str) -> None:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                self._collect_targets(node_id, target, "assignment")
            self._collect_uses(node_id, stmt.value)
        elif isinstance(stmt, ast.AnnAssign):
            self._collect_targets(node_id, stmt.target, "assignment")
            self._collect_uses(node_id, stmt.value)
        elif isinstance(stmt, ast.AugAssign):
            if isinstance(stmt.target, ast.Name):
                self._add_use(node_id, stmt.target.id, stmt.target)
                self._add_def(node_id, stmt.target.id, "augmented_assignment", stmt.target)
            else:
                self._collect_uses(node_id, stmt.target)
            self._collect_uses(node_id, stmt.value)
        elif isinstance(stmt, ast.Delete):
            for target in stmt.targets:
                self._collect_targets(node_id, target, "delete")
        elif isinstance(stmt, ast.Return):
            self._collect_uses(node_id, stmt.value)
        elif isinstance(stmt, ast.Raise):
            self._collect_uses(node_id, stmt.exc)
            self._collect_uses(node_id, stmt.cause)
        elif isinstance(stmt, ast.Assert):
            self._collect_uses(node_id, stmt.test)
            self._collect_uses(node_id, stmt.msg)
        elif isinstance(stmt, ast.Expr):
            self._collect_uses(node_id, stmt.value)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                bound = alias.asname or alias.name.split(".")[0]
                self._add_def(node_id, bound, "import", stmt)
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                self._add_def(node_id, bound, "import", stmt)
        elif isinstance(stmt, (ast.Global, ast.Nonlocal)):
            self.facts.non_local.update(stmt.names)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._add_def(node_id, stmt.name, "nested_def", stmt)
            for decorator in stmt.decorator_list:
                self._collect_uses(node_id, decorator)
            for default in stmt.args.defaults:
                self._collect_uses(node_id, default)
            for kw_default in stmt.args.kw_defaults:
                self._collect_uses(node_id, kw_default)
        elif isinstance(stmt, ast.ClassDef):
            self._add_def(node_id, stmt.name, "nested_def", stmt)
            for decorator in stmt.decorator_list:
                self._collect_uses(node_id, decorator)
            for base in stmt.bases:
                self._collect_uses(node_id, base)
            for keyword in stmt.keywords:
                self._collect_uses(node_id, keyword.value)
        elif isinstance(stmt, ast.Pass):
            return
        else:
            # Any remaining simple statement: treat its expressions as reads.
            self._collect_uses(node_id, stmt)

    def _collect_pattern(self, node_id: str, pattern: ast.pattern) -> None:
        if not node_id:
            return
        if isinstance(pattern, ast.MatchValue):
            self._collect_uses(node_id, pattern.value)
        elif isinstance(pattern, ast.MatchSequence):
            for sub in pattern.patterns:
                self._collect_pattern(node_id, sub)
        elif isinstance(pattern, ast.MatchMapping):
            for key in pattern.keys:
                self._collect_uses(node_id, key)
            for sub in pattern.patterns:
                self._collect_pattern(node_id, sub)
            if pattern.rest:
                self._add_def(node_id, pattern.rest, "pattern", pattern)
        elif isinstance(pattern, ast.MatchClass):
            self._collect_uses(node_id, pattern.cls)
            for sub in pattern.patterns:
                self._collect_pattern(node_id, sub)
            for sub in pattern.kwd_patterns:
                self._collect_pattern(node_id, sub)
        elif isinstance(pattern, ast.MatchStar):
            if pattern.name:
                self._add_def(node_id, pattern.name, "pattern", pattern)
        elif isinstance(pattern, ast.MatchAs):
            if pattern.pattern is not None:
                self._collect_pattern(node_id, pattern.pattern)
            if pattern.name:
                self._add_def(node_id, pattern.name, "pattern", pattern)
        elif isinstance(pattern, ast.MatchOr):
            for sub in pattern.patterns:
                self._collect_pattern(node_id, sub)

    def _collect_parameters(self, entry_id: str) -> None:
        args = self.func.args
        all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg is not None:
            all_args.append(args.vararg)
        if args.kwarg is not None:
            all_args.append(args.kwarg)
        for arg in all_args:
            self._add_def(entry_id, arg.arg, "parameter", arg)

    # -------------------------------------------------------------- traversal

    def build(self) -> None:
        self.entry = self._new_node(
            CFGNodeKind.ENTRY,
            "entry",
            self.func,
            line=self.func.lineno,
            end_line=self.func.lineno,
        )
        self.exit = self._new_node(
            CFGNodeKind.EXIT,
            "exit",
            self.func,
            line=getattr(self.func, "end_lineno", self.func.lineno),
            end_line=getattr(self.func, "end_lineno", self.func.lineno),
        )
        self._collect_parameters(self.entry)
        frontier = {self.entry} if self.entry else set()
        end = self._block(self.func.body, frontier, 0, CFGEdgeType.NORMAL)
        for node_id in sorted(end):
            self._edge(node_id, self.exit, CFGEdgeType.NORMAL)

    def _block(
        self,
        stmts: list[ast.stmt],
        frontier: set[str],
        depth: int,
        edge_type: CFGEdgeType,
    ) -> set[str]:
        current = set(frontier)
        for index, stmt in enumerate(stmts):
            if not current:
                remaining = stmts[index:]
                if remaining:
                    self._notes.add(
                        f"unreachable statements after line {getattr(remaining[0], 'lineno', '?')}"
                        " are omitted"
                    )
                break
            current = self._stmt(
                stmt,
                current,
                depth,
                edge_type if index == 0 else CFGEdgeType.NORMAL,
            )
        return current

    def _stmt(
        self,
        stmt: ast.stmt,
        frontier: set[str],
        depth: int,
        edge_type: CFGEdgeType,
    ) -> set[str]:
        if depth > self.max_depth:
            self.truncated = True
            self._notes.add(f"maximum traversal depth {self.max_depth} reached")
            return set()

        if isinstance(stmt, ast.If):
            return self._if(stmt, frontier, depth, edge_type)
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            return self._for(stmt, frontier, depth, edge_type)
        if isinstance(stmt, ast.While):
            return self._while(stmt, frontier, depth, edge_type)
        _ast = _ast_module()
        if isinstance(stmt, ast.Try) or (
            hasattr(ast, "TryStar") and isinstance(stmt, _ast.TryStar)
        ):
            return self._try(stmt, frontier, depth, edge_type)
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            return self._with(stmt, frontier, depth, edge_type)
        if getattr(ast, "Match", None) is not None and isinstance(stmt, ast.Match):
            return self._match(stmt, frontier, depth, edge_type)
        return self._simple(stmt, frontier, edge_type)

    def _simple(self, stmt: ast.stmt, frontier: set[str], edge_type: CFGEdgeType) -> set[str]:
        kind = CFGNodeKind.STATEMENT
        if isinstance(stmt, ast.Return):
            kind = CFGNodeKind.RETURN
        elif isinstance(stmt, ast.Raise):
            kind = CFGNodeKind.RAISE
        elif isinstance(stmt, ast.Break):
            kind = CFGNodeKind.BREAK
        elif isinstance(stmt, ast.Continue):
            kind = CFGNodeKind.CONTINUE
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = CFGNodeKind.NESTED_DEF
        node_id = self._new_node(kind, _stmt_label(stmt), stmt)
        if not node_id:
            return set()
        self._connect(frontier, node_id, edge_type)
        self._collect_simple(stmt, node_id)

        if kind is CFGNodeKind.RETURN:
            self._edge(node_id, self.exit, CFGEdgeType.RETURN, line=stmt.lineno)
            return set()
        if kind is CFGNodeKind.RAISE:
            self._edge(node_id, self.exit, CFGEdgeType.RAISE, line=stmt.lineno)
            return set()
        if kind is CFGNodeKind.BREAK:
            if self._loops:
                self._edge(
                    node_id, self._loops[-1].break_target, CFGEdgeType.BREAK, line=stmt.lineno
                )
            else:
                self._notes.add("break outside a modelled loop")
            return set()
        if kind is CFGNodeKind.CONTINUE:
            if self._loops:
                self._edge(
                    node_id, self._loops[-1].continue_target, CFGEdgeType.CONTINUE, line=stmt.lineno
                )
            else:
                self._notes.add("continue outside a modelled loop")
            return set()
        return {node_id}

    def _if(
        self,
        stmt: ast.If,
        frontier: set[str],
        depth: int,
        edge_type: CFGEdgeType,
    ) -> set[str]:
        cond = self._new_node(CFGNodeKind.CONDITION, f"if {_unparse(stmt.test)}", stmt)
        if not cond:
            return set()
        self._connect(frontier, cond, edge_type)
        self._collect_uses(cond, stmt.test)
        body_end = self._block(stmt.body, {cond}, depth + 1, CFGEdgeType.TRUE)
        else_end = (
            self._block(stmt.orelse, {cond}, depth + 1, CFGEdgeType.FALSE) if stmt.orelse else set()
        )
        true_continues = bool(body_end) or not stmt.body
        false_continues = bool(else_end) or not stmt.orelse
        if not true_continues and not false_continues:
            return set()
        merge = self._new_node(CFGNodeKind.MERGE, "if-merge", stmt)
        if not merge:
            return body_end | else_end
        if body_end:
            for node_id in sorted(body_end):
                self._edge(node_id, merge, CFGEdgeType.NORMAL, line=stmt.lineno)
        elif not stmt.body:
            self._edge(cond, merge, CFGEdgeType.TRUE, line=stmt.lineno)
        if else_end:
            for node_id in sorted(else_end):
                self._edge(node_id, merge, CFGEdgeType.NORMAL, line=stmt.lineno)
        elif not stmt.orelse:
            self._edge(cond, merge, CFGEdgeType.FALSE, line=stmt.lineno)
        return {merge}

    def _for(
        self,
        stmt: ast.For | ast.AsyncFor,
        frontier: set[str],
        depth: int,
        edge_type: CFGEdgeType,
    ) -> set[str]:
        header = self._new_node(
            CFGNodeKind.LOOP, f"for {_unparse(stmt.target)} in {_unparse(stmt.iter)}", stmt
        )
        if not header:
            return set()
        self._connect(frontier, header, edge_type)
        self._collect_uses(header, stmt.iter)
        self._collect_targets(header, stmt.target, "loop_target")
        exit_merge = self._new_node(CFGNodeKind.MERGE, "loop-exit", stmt)
        if not exit_merge:
            return set()
        self._loops.append(_LoopContext(header, exit_merge, header))
        body_end = self._block(stmt.body, {header}, depth + 1, CFGEdgeType.LOOP_BODY)
        for node_id in sorted(body_end):
            self._edge(node_id, header, CFGEdgeType.LOOP_BACK, line=stmt.lineno)
        if stmt.orelse:
            else_end = self._block(stmt.orelse, {header}, depth + 1, CFGEdgeType.LOOP_EXIT)
            for node_id in sorted(else_end):
                self._edge(node_id, exit_merge, CFGEdgeType.NORMAL, line=stmt.lineno)
        else:
            self._edge(header, exit_merge, CFGEdgeType.LOOP_EXIT, line=stmt.lineno)
        self._loops.pop()
        if (
            stmt.orelse
            and not else_end
            and not any(edge.target == exit_merge for edge in self.edges)
        ):
            self.nodes = [node for node in self.nodes if node.id != exit_merge]
            return set()
        return {exit_merge}

    def _while(
        self,
        stmt: ast.While,
        frontier: set[str],
        depth: int,
        edge_type: CFGEdgeType,
    ) -> set[str]:
        header = self._new_node(CFGNodeKind.LOOP, f"while {_unparse(stmt.test)}", stmt)
        if not header:
            return set()
        self._connect(frontier, header, edge_type)
        self._collect_uses(header, stmt.test)
        exit_merge = self._new_node(CFGNodeKind.MERGE, "loop-exit", stmt)
        if not exit_merge:
            return set()
        self._loops.append(_LoopContext(header, exit_merge, header))
        body_end = self._block(stmt.body, {header}, depth + 1, CFGEdgeType.LOOP_BODY)
        for node_id in sorted(body_end):
            self._edge(node_id, header, CFGEdgeType.LOOP_BACK, line=stmt.lineno)
        if stmt.orelse:
            else_end = self._block(stmt.orelse, {header}, depth + 1, CFGEdgeType.LOOP_EXIT)
            for node_id in sorted(else_end):
                self._edge(node_id, exit_merge, CFGEdgeType.NORMAL, line=stmt.lineno)
        else:
            self._edge(header, exit_merge, CFGEdgeType.LOOP_EXIT, line=stmt.lineno)
        self._loops.pop()
        if (
            stmt.orelse
            and not else_end
            and not any(edge.target == exit_merge for edge in self.edges)
        ):
            self.nodes = [node for node in self.nodes if node.id != exit_merge]
            return set()
        return {exit_merge}

    def _try(
        self,
        stmt: ast.Try | ast.TryStar,
        frontier: set[str],
        depth: int,
        edge_type: CFGEdgeType,
    ) -> set[str]:
        header = self._new_node(CFGNodeKind.TRY, "try", stmt)
        if not header:
            return set()
        self._connect(frontier, header, edge_type)
        if stmt.handlers:
            self._notes.add(
                "try/except exception edges are conservative: every try-body node is linked to"
                " every handler; exception types and propagation are not modelled"
            )
        body_start = len(self.nodes)
        body_end = self._block(stmt.body, {header}, depth + 1, CFGEdgeType.NORMAL)
        # Use the nodes that remain after building the body. A nested
        # exhaustive match (or terminal loop/finally) can build a synthetic
        # merge node and then remove it again; a sequence-number range would
        # include that removed id and create an edge from a non-existent node.
        body_nodes = [node.id for node in self.nodes[body_start:]]
        handler_ends: list[str] = []
        for handler in stmt.handlers:
            handler_node = self._new_node(
                CFGNodeKind.HANDLER,
                _handler_label(handler),
                handler,
                line=getattr(handler, "lineno", stmt.lineno),
            )
            if not handler_node:
                continue
            for body_node in [header, *body_nodes]:
                self._edge(
                    body_node,
                    handler_node,
                    CFGEdgeType.EXCEPTION,
                    line=getattr(handler, "lineno", stmt.lineno),
                    evidence="conservative: any try-body statement may raise into this handler",
                )
            self._collect_uses(handler_node, handler.type)
            if handler.name:
                self._add_def(handler_node, handler.name, "except_name", handler)
            handler_end = self._block(handler.body, {handler_node}, depth + 1, CFGEdgeType.NORMAL)
            handler_ends.extend(sorted(handler_end))
        if stmt.orelse:
            else_end = self._block(stmt.orelse, body_end, depth + 1, CFGEdgeType.NORMAL)
        else:
            else_end = set(body_end)
        if stmt.finalbody:
            finally_inputs = else_end | set(handler_ends)
            if not finally_inputs:
                # Return/raise/break/continue inside try do not route through
                # finally in this model, so all modeled paths have terminated.
                self._notes.add(
                    "try/finally body omitted because every modeled try path terminates"
                )
                return set()
            finally_node = self._new_node(CFGNodeKind.TRY, "finally", stmt)
            if not finally_node:
                return set()
            for node_id in sorted(finally_inputs):
                self._edge(node_id, finally_node, CFGEdgeType.FINALLY, line=stmt.lineno)
            after = self._block(stmt.finalbody, {finally_node}, depth + 1, CFGEdgeType.NORMAL)
        else:
            after = else_end | set(handler_ends)
        if not after:
            return set()
        merge = self._new_node(CFGNodeKind.MERGE, "try-merge", stmt)
        if not merge:
            return set()
        for node_id in sorted(after):
            self._edge(node_id, merge, CFGEdgeType.NORMAL, line=stmt.lineno)
        return {merge}

    def _with(
        self,
        stmt: ast.With | ast.AsyncWith,
        frontier: set[str],
        depth: int,
        edge_type: CFGEdgeType,
    ) -> set[str]:
        label = "with " + ", ".join(_unparse(item.context_expr) for item in stmt.items)
        node_id = self._new_node(CFGNodeKind.WITH, label, stmt)
        if not node_id:
            return set()
        self._connect(frontier, node_id, edge_type)
        for item in stmt.items:
            self._collect_uses(node_id, item.context_expr)
            if item.optional_vars is not None:
                self._collect_targets(node_id, item.optional_vars, "with_target")
        body_end = self._block(stmt.body, {node_id}, depth + 1, CFGEdgeType.NORMAL)
        if not body_end:
            return set()
        merge = self._new_node(CFGNodeKind.MERGE, "with-merge", stmt)
        if not merge:
            return set()
        for end in sorted(body_end):
            self._edge(end, merge, CFGEdgeType.NORMAL, line=stmt.lineno)
        return {merge}

    def _match(
        self,
        stmt: ast.Match,
        frontier: set[str],
        depth: int,
        edge_type: CFGEdgeType,
    ) -> set[str]:
        node_id = self._new_node(CFGNodeKind.MATCH, f"match {_unparse(stmt.subject)}", stmt)
        if not node_id:
            return set()
        self._connect(frontier, node_id, edge_type)
        self._collect_uses(node_id, stmt.subject)
        self._notes.add(
            "match cases are modelled as structural branches; pattern specificity and"
            " evaluation order are not evaluated"
        )
        merge = self._new_node(CFGNodeKind.MERGE, "match-merge", stmt)
        if not merge:
            return set()
        continuation = False
        exhaustive = False
        for case in stmt.cases:
            case_node = self._new_node(
                CFGNodeKind.CASE,
                _case_label(case),
                case,
                line=getattr(case.pattern, "lineno", stmt.lineno),
                end_line=max(
                    [getattr(body, "end_lineno", stmt.lineno) or stmt.lineno for body in case.body]
                    or [stmt.lineno]
                ),
            )
            if not case_node:
                continue
            self._edge(
                node_id,
                case_node,
                CFGEdgeType.CASE,
                line=getattr(case.pattern, "lineno", stmt.lineno),
            )
            self._collect_pattern(case_node, case.pattern)
            if case.guard is not None:
                self._collect_uses(case_node, case.guard, after_defs=True)
            case_end = self._block(case.body, {case_node}, depth + 1, CFGEdgeType.NORMAL)
            for end in sorted(case_end):
                self._edge(end, merge, CFGEdgeType.NORMAL, line=stmt.lineno)
                continuation = True
            # A guard can fail even for a wildcard/capture pattern, so only an
            # unguarded irrefutable pattern makes the match exhaustive.
            exhaustive = exhaustive or (case.guard is None and _is_irrefutable(case.pattern))
        if not exhaustive:
            self._edge(node_id, merge, CFGEdgeType.NO_MATCH, line=stmt.lineno)
            continuation = True
        if not continuation:
            # No case falls through and there is no no-match path, so the
            # synthetic merge is unreachable; do not leave an orphan node.
            self.nodes = [node for node in self.nodes if node.id != merge]
            return set()
        return {merge}


def _ast_module() -> Any:
    return ast


def _expression_facts(
    expr: ast.AST,
) -> tuple[list[tuple[str, int, int, bool]], list[tuple[str, ast.NamedExpr]]]:
    """Collect outer-scope name reads and walrus targets in evaluation order.

    Each use carries ``after_walrus=True`` when a walrus target with the same
    name was already evaluated earlier in this expression. Lambda bodies and
    comprehension internals have their own scopes; only the first comprehension
    iterable (and lambda defaults) are evaluated in the enclosing scope. They are
    collected; everything else inside those scopes is deliberately skipped and
    documented as a limitation.
    """
    uses: list[tuple[str, int, int, bool]] = []
    walrus_targets: list[tuple[str, ast.NamedExpr]] = []
    bound_by_walrus: set[str] = set()

    def visit(node: ast.AST, force_after: bool = False) -> None:
        if isinstance(node, ast.Lambda):
            for default in node.args.defaults:
                visit(default, force_after)
            for kw_default in node.args.kw_defaults:
                if kw_default is not None:
                    visit(kw_default, force_after)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            if node.generators:
                visit(node.generators[0].iter, force_after)
            return
        if isinstance(node, ast.NamedExpr):
            visit(node.value, force_after)
            if isinstance(node.target, ast.Name):
                walrus_targets.append((node.target.id, node))
                bound_by_walrus.add(node.target.id)
            return
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                uses.append(
                    (
                        node.id,
                        int(node.lineno),
                        int(node.col_offset),
                        force_after or node.id in bound_by_walrus,
                    )
                )
            return
        if isinstance(node, ast.IfExp):
            # Only one branch executes. A walrus in the body is not visible in
            # the else expression, but either branch may bind a name for a use
            # after the whole conditional expression.
            visit(node.test, force_after)
            before_branches = set(bound_by_walrus)
            visit(node.body, force_after)
            body_bound = set(bound_by_walrus)
            bound_by_walrus.clear()
            bound_by_walrus.update(before_branches)
            visit(node.orelse, force_after)
            bound_by_walrus.update(body_bound)
            return
        if isinstance(node, ast.Dict):
            # Dict keys and values are evaluated pairwise, not all keys before
            # all values, so preserve that order for walrus visibility.
            for key, value in zip(node.keys, node.values, strict=True):
                if key is not None:
                    visit(key, force_after)
                visit(value, force_after)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, force_after)

    visit(expr)
    return uses, walrus_targets


def _handler_label(handler: ast.ExceptHandler) -> str:
    text = f"except {_unparse(handler.type)}" if handler.type is not None else "except"
    if handler.name:
        text += f" as {handler.name}"
    return text


def _case_label(case: ast.match_case) -> str:
    text = f"case {_unparse(case.pattern)}"
    if case.guard is not None:
        text += f" if {_unparse(case.guard)}"
    return text


def _is_irrefutable(pattern: ast.pattern) -> bool:
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None or _is_irrefutable(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return any(_is_irrefutable(sub) for sub in pattern.patterns)
    return False


def build_function_cfg_with_facts(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str] | None,
    *,
    symbol_id: str = "",
    qualified_name: str = "",
    file_id: str = "",
    rel_path: str = "",
    source_changed_since_analysis: bool = False,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> CFGWithFacts:
    """Build the CFG and per-node def/use facts for one function."""
    builder = _Builder(
        func_node,
        source_lines,
        max_nodes=max_nodes,
        max_edges=max_edges,
        max_depth=max_depth,
    )
    builder.build()
    notes = sorted({*builder._notes, *builder.facts.notes})
    cfg = FunctionCFG(
        symbol_id=symbol_id,
        qualified_name=qualified_name,
        file_id=file_id,
        rel_path=rel_path,
        start_line=func_node.lineno,
        end_line=getattr(func_node, "end_lineno", func_node.lineno),
        entry=builder.entry,
        exit=builder.exit,
        nodes=builder.nodes,
        edges=builder.edges,
        truncated=builder.truncated,
        source_changed_since_analysis=source_changed_since_analysis,
        notes=notes,
    )
    builder.facts.notes = set(notes)
    return CFGWithFacts(cfg=cfg, facts=builder.facts)


def build_function_cfg(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str] | None,
    **kwargs: Any,
) -> FunctionCFG:
    """Build only the public CFG model for one function."""
    return build_function_cfg_with_facts(func_node, source_lines, **kwargs).cfg
