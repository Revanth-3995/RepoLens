"""Bounded local data-flow analysis on a function CFG (Phase 2, P2-03).

Semantics (precise scope, also in ``docs/phase-2-plan.md``):

- **Local, intraprocedural.** Only names bound or read inside one function are
  analysed. Calls are not followed; no interprocedural, alias, points-to, or
  whole-program analysis is claimed.
- A **definition** is a local binding: parameter, assignment/annotation/delete
  target, augmented assignment, loop target, ``with ... as`` target,
  ``except ... as`` name, import alias, nested ``def``/``class`` name, walrus
  target, or ``match`` pattern binding.
- A **use** is a ``Name`` in load context evaluated in the function's own scope.
  Lambda bodies and comprehension interiors have their own scopes and are not
  analysed; the first comprehension iterable and lambda defaults are collected
  because they are evaluated in the enclosing scope.
- **Reaching definitions** is a forward *may* analysis:
  ``OUT[n] = GEN[n] ∪ (IN[n] − KILL[n])`` and
  ``IN[n] = ⋃ OUT[p] for p ∈ pred(n)``. A use sees ``IN`` (so ``a = a + 1``
  reads the previous ``a``); match-case guards see the pattern bindings on the
  same node.
- Branch joins union the incoming definition sets; loops iterate to a fixpoint
  (bounded). ``undefined_uses`` means "no locally reaching definition", not
  "undefined at runtime": the name may be a global, builtin, closure variable,
  or comprehension variable.
- Exception edges are the CFG's conservative edges, so a definition before a
  possible raise may reach a handler. ``return``/``break``/``continue`` paths
  that bypass ``finally`` follow the CFG approximation.

The analysis is bounded by node/definition/use/iteration caps and reports
``truncated=True`` instead of silently claiming completeness.
"""

from __future__ import annotations

from collections import defaultdict, deque

from repolens.analysis.cfg import CFGWithFacts, DefinitionDraft, UseDraft
from repolens.core.models import (
    DataFlowResult,
    DefinitionFact,
    DefinitionKind,
    DefUseLink,
    ReachingDefinitions,
    UseFact,
)

DEFAULT_MAX_DEFINITIONS = 5_000
DEFAULT_MAX_USES = 10_000
DEFAULT_MAX_ITERATIONS = 50_000


def _to_definition_kind(kind: str) -> DefinitionKind:
    try:
        return DefinitionKind(kind)
    except ValueError:  # defensive: unknown kinds are never invented
        return DefinitionKind.ASSIGNMENT


def analyze_dataflow(
    built: CFGWithFacts,
    *,
    max_definitions: int = DEFAULT_MAX_DEFINITIONS,
    max_uses: int = DEFAULT_MAX_USES,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> DataFlowResult:
    """Compute local reaching definitions and def-use links for one CFG."""
    cfg = built.cfg
    facts = built.facts
    definitions: list[DefinitionFact] = []
    uses: list[UseFact] = []
    def_drafts: dict[str, DefinitionDraft] = {}
    use_drafts: dict[str, UseDraft] = {}
    # Facts are derived from this CFG: if the CFG was capped, the data-flow
    # result is incomplete too even when its own collection caps were not hit.
    truncated = cfg.truncated
    collection_truncated = False
    notes: set[str] = set(cfg.notes)

    def_order: dict[str, int] = {}
    use_order: dict[str, int] = {}
    for node in cfg.nodes:
        for def_draft in facts.node_defs.get(node.id, []):
            if len(definitions) >= max_definitions:
                truncated = True
                collection_truncated = True
                break
            did = f"d{len(definitions)}"
            def_order[did] = len(definitions)
            def_drafts[did] = def_draft
            definitions.append(
                DefinitionFact(
                    id=did,
                    name=def_draft.name,
                    kind=_to_definition_kind(def_draft.kind),
                    node_id=node.id,
                    line=def_draft.line,
                    column=def_draft.column,
                )
            )
        for use_draft in facts.node_uses.get(node.id, []):
            if len(uses) >= max_uses:
                truncated = True
                collection_truncated = True
                break
            uid = f"u{len(uses)}"
            use_order[uid] = len(uses)
            use_drafts[uid] = use_draft
            uses.append(
                UseFact(
                    id=uid,
                    name=use_draft.name,
                    node_id=node.id,
                    line=use_draft.line,
                    column=use_draft.column,
                )
            )

    if collection_truncated:
        notes.add("definition/use collection cap reached; data-flow result is incomplete")

    defs_by_node: dict[str, list[DefinitionFact]] = defaultdict(list)
    definition_by_id = {definition.id: definition for definition in definitions}
    for definition in definitions:
        defs_by_node[definition.node_id].append(definition)
    gen: dict[str, dict[str, frozenset[str]]] = {}
    for node in cfg.nodes:
        node_gen: dict[str, set[str]] = defaultdict(set)
        for definition in defs_by_node.get(node.id, []):
            node_gen[definition.name].add(definition.id)
        gen[node.id] = {name: frozenset(ids) for name, ids in node_gen.items()}

    # Predecessor/successor maps: parallel edge types collapse for data flow.
    preds: dict[str, set[str]] = defaultdict(set)
    succs: dict[str, set[str]] = defaultdict(set)
    node_ids = [node.id for node in cfg.nodes]
    for edge in cfg.edges:
        preds[edge.target].add(edge.source)
        succs[edge.source].add(edge.target)

    state_in: dict[str, dict[str, frozenset[str]]] = {nid: {} for nid in node_ids}
    state_out: dict[str, dict[str, frozenset[str]]] = {nid: {} for nid in node_ids}
    worklist: deque[str] = deque(node_ids)
    queued: set[str] = set(node_ids)
    iterations = 0
    while worklist:
        if iterations >= max_iterations:
            truncated = True
            notes.add(f"fixpoint iteration cap {max_iterations} reached")
            break
        iterations += 1
        nid = worklist.popleft()
        queued.discard(nid)

        incoming: dict[str, frozenset[str]] = {}
        for pred in sorted(preds.get(nid, set())):
            for name, ids in state_out[pred].items():
                incoming[name] = incoming.get(name, frozenset()) | ids
        state_in[nid] = incoming

        outgoing = dict(incoming)
        for name, ids in gen[nid].items():
            outgoing[name] = ids
        if outgoing != state_out[nid]:
            state_out[nid] = outgoing
            for succ in sorted(succs.get(nid, set())):
                if succ not in queued:
                    worklist.append(succ)
                    queued.add(succ)

    reaching: list[ReachingDefinitions] = []
    def_to_uses: dict[str, list[str]] = defaultdict(list)
    undefined: list[str] = []
    non_local = set(facts.non_local)
    for use in uses:
        use_draft = use_drafts[use.id]
        state = state_out[use.node_id] if use_draft.after_defs else state_in[use.node_id]
        reach = state.get(use.name, frozenset())
        if use_draft.after_defs:
            # A same-node use after a walrus target must not see a same-name
            # walrus that appears *later* in the same expression. Node-level
            # OUT contains every definition on the node, so use source order to
            # discard definitions that have not executed yet at this use.
            cutoff = (use_draft.line, use_draft.column)
            reach = frozenset(
                did
                for did in reach
                if not (
                    definition_by_id[did].node_id == use.node_id
                    and (def_drafts[did].line, def_drafts[did].column) > cutoff
                )
            )
        ordered = sorted(reach, key=lambda did: def_order.get(did, 0))
        reaching.append(ReachingDefinitions(use_id=use.id, definitions=ordered))
        if not ordered and use.name not in non_local:
            undefined.append(use.id)
        for did in ordered:
            def_to_uses[did].append(use.id)

    def_use: list[DefUseLink] = []
    for definition in definitions:
        linked = sorted(def_to_uses.get(definition.id, []), key=lambda uid: use_order.get(uid, 0))
        def_use.append(DefUseLink(definition_id=definition.id, uses=linked))

    if undefined:
        notes.add(
            "uses without a locally reaching definition may be globals, builtins,"
            " closure variables, or comprehension variables; they are not claimed"
            " to be runtime errors"
        )
    notes.add("lambda bodies and comprehension interiors are separate scopes and are not analysed")
    if any(definition.kind is DefinitionKind.AUGMENTED_ASSIGNMENT for definition in definitions):
        notes.add("augmented-assignment reads are modelled before the same-node definition")

    return DataFlowResult(
        symbol_id=cfg.symbol_id,
        qualified_name=cfg.qualified_name,
        file_id=cfg.file_id,
        rel_path=cfg.rel_path,
        definitions=definitions,
        uses=uses,
        reaching=reaching,
        def_use=def_use,
        undefined_uses=undefined,
        non_local_names=sorted(non_local),
        truncated=truncated,
        source_changed_since_analysis=cfg.source_changed_since_analysis,
        notes=sorted(notes),
    )
