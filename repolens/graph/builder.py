"""Graph construction from repository facts (NetworkX DiGraph).

Node/edge semantics (docs/architecture.md section 8):
- Nodes: ``repository``, ``file`` (module ≡ file node, AD-4), ``class``,
  ``function``, ``method``. Node ids are the domain ids (repo_*, file_*,
  sym_*).
- Edges (directed): ``contains`` (repo->file, file->top-level symbol,
  symbol->nested symbol), ``imports`` (file->file of the imported module),
  ``calls`` (origin symbol -> target symbol; a module-level call originates
  at the file node because the module symbol IS the file node),
  ``inherits_from`` (class->class).
- Only relationships with resolution status ``resolved_static`` become
  edges. Unresolved/external/ambiguous/dynamic relationships stay database
  records and are never fabricated into nodes or edges.
- Multiple relationships between the same node pair and type merge into one
  edge carrying a ``sites`` provenance list (no duplicate edges).
- Every relationship whose target cannot be mapped to an existing node is a
  :class:`GraphIntegrityError` (fail loudly, never silently drop).

Both inputs are supported: in-memory pipeline drafts (SymbolDraft,
RelationshipDraft, FileAnalysis) and persisted rows (SymbolRow, RelRow,
FileRow) — they share the same attribute names where it matters.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any

import networkx as nx

from repolens.core.errors import GraphIntegrityError
from repolens.core.models import (
    RelationshipDraft,
    RelationshipType,
    ResolutionStatus,
)


def _value(x: Any) -> Any:
    return x.value if isinstance(x, Enum) else x


@dataclass
class GraphBuilder:
    """Builds and validates a DiGraph from facts of one repository run."""

    repo_id: str
    files: list[Any]
    symbols: list[Any]
    relationships: list[RelationshipDraft] | list[Any]
    node_prefix: str = ""  # unused; ids are already prefixed

    def build(self) -> nx.MultiDiGraph:
        # MultiDiGraph: a node pair may carry several distinct relationship
        # types (e.g. inherits_from + calls). A plain DiGraph would silently
        # overwrite one with the other via add_edge.
        g = nx.MultiDiGraph()
        g.graph["repo_id"] = self.repo_id
        repo_node = f"repo_{self.repo_id}"
        g.add_node(repo_node, type="repository", label=self.repo_id)

        # ---- file nodes -------------------------------------------------
        file_rel: dict[str, str] = {}
        module_to_file: dict[str, str] = {}
        for f in self.files:
            fid = getattr(f, "file_id", None) or getattr(f, "id", "")
            rel = getattr(f, "rel_path", "")
            mod = getattr(f, "module_name", "")
            if not fid:
                raise GraphIntegrityError("file row without id")
            file_rel[fid] = rel
            module_to_file[mod] = fid
            g.add_node(fid, type="file", label=rel, rel_path=rel, module_name=mod)
            g.add_edge(repo_node, fid, type="contains", sites=[])

        # ---- symbol nodes ----------------------------------------------
        symbol_meta: dict[str, dict[str, str]] = {}  # symbol_id -> {kind, file_id, qname}
        for s in self.symbols:
            sid = str(getattr(s, "id", None) or getattr(s, "symbol_id", ""))
            kind = str(_value(getattr(s, "kind", "")))
            if kind == "module":
                # module symbols are represented by their file node (AD-4)
                symbol_meta[sid] = {
                    "kind": kind,
                    "file_id": getattr(s, "file_id", ""),
                    "qualified_name": getattr(s, "qualified_name", ""),
                }
                continue
            fid = str(getattr(s, "file_id", ""))
            if fid not in file_rel:
                raise GraphIntegrityError(f"symbol {sid} references unknown file {fid}")
            qn = str(getattr(s, "qualified_name", ""))
            symbol_meta[sid] = {"kind": kind, "file_id": fid, "qualified_name": qn}
            g.add_node(
                sid,
                type=kind,
                label=qn,
                qualified_name=qn,
                rel_path=file_rel[fid],
                file_id=fid,
            )

        for s in self.symbols:
            sid = str(getattr(s, "id", None) or getattr(s, "symbol_id", ""))
            kind = str(_value(getattr(s, "kind", "")))
            if kind == "module":
                continue
            parent = getattr(s, "parent_id", None)
            if parent is None:
                # top-level symbol: contained by its file node
                g.add_edge(getattr(s, "file_id", ""), sid, type="contains", sites=[])
            else:
                # nested: parent may be a module symbol? no: module parent -> file node
                parent_kind = symbol_meta.get(parent, {}).get("kind")
                parent_node = symbol_meta[parent]["file_id"] if parent_kind == "module" else parent
                if parent_node not in g or sid not in g:
                    raise GraphIntegrityError(
                        f"contains edge references missing node: {parent_node} -> {sid}"
                    )
                g.add_edge(parent_node, sid, type="contains", sites=[])

        # ---- relationship edges (resolved_static only) ------------------
        pending: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for r in self.relationships:
            status = _value(getattr(r, "resolution_status", None))
            if status != ResolutionStatus.RESOLVED_STATIC.value:
                continue
            rtype = _value(getattr(r, "rel_type", None))
            src_file = getattr(r, "source_file_id", "")
            src_symbol = getattr(r, "source_symbol_id", None)
            tgt_symbol = getattr(r, "target_symbol_id", None)
            tgt_ref = getattr(r, "target_ref", "")
            line = getattr(r, "source_line", None)
            evidence = getattr(r, "evidence", "")

            if rtype == RelationshipType.IMPORTS.value:
                # file -> file edges
                src = src_file
                tgt: str | None = None
                if tgt_symbol and tgt_symbol in symbol_meta:
                    tgt = symbol_meta[tgt_symbol]["file_id"]
                elif tgt_ref in module_to_file:
                    tgt = module_to_file[tgt_ref]
            elif rtype == RelationshipType.CALLS.value:
                # source: symbol node, or file node for module-level code
                if src_symbol and src_symbol in symbol_meta:
                    skind = symbol_meta[src_symbol]["kind"]
                    src = symbol_meta[src_symbol]["file_id"] if skind == "module" else src_symbol
                else:
                    src = src_file
                tgt = tgt_symbol
                if tgt and tgt in symbol_meta and symbol_meta[tgt]["kind"] == "module":
                    tgt = symbol_meta[tgt]["file_id"]
            elif rtype == RelationshipType.INHERITS_FROM.value:
                src = src_symbol
                tgt = tgt_symbol
            else:
                raise GraphIntegrityError(f"unknown relationship type: {rtype}")

            if not src or not tgt:
                raise GraphIntegrityError(
                    f"relationship {getattr(r, 'relationship_id', getattr(r, 'id', '?'))}"
                    f" cannot be mapped to graph nodes (type={rtype})"
                )
            pending[(src, tgt, rtype)].append(
                {"line": line, "evidence": evidence, "source_symbol_id": src_symbol}
            )

        for (src, tgt, rtype), sites in pending.items():
            if src not in g or tgt not in g:
                raise GraphIntegrityError(
                    f"edge references non-existent node: src={src} tgt={tgt} type={rtype}"
                )
            # Merge into an existing parallel edge of the SAME type; otherwise
            # add a new parallel edge so distinct types coexist (no overwrite).
            existing_key = None
            edge_data = g.get_edge_data(src, tgt) or {}
            for key, data in edge_data.items():
                if data.get("type") == rtype:
                    existing_key = key
                    break
            if existing_key is not None:
                g.edges[src, tgt, existing_key]["sites"].extend(sites)
            else:
                g.add_edge(src, tgt, type=rtype, sites=sites)

        self._validate(g)
        return g

    def _validate(self, g: nx.DiGraph) -> None:
        """Structural integrity checks (fail the run on violation)."""
        for src, tgt, data in g.edges(data=True):
            if not g.has_node(src) or not g.has_node(tgt):
                raise GraphIntegrityError("edge with missing endpoint")
            if not data.get("type"):
                raise GraphIntegrityError(f"edge {src}->{tgt} without type")
        for nid, data in g.nodes(data=True):
            ntype = data.get("type")
            if ntype in {"class", "function", "method"}:
                if data.get("file_id") not in g:
                    raise GraphIntegrityError(f"symbol node {nid} has no file node")
                if not any(
                    ed.get("type") == "contains"
                    for p in g.predecessors(nid)
                    for ed in (g.get_edge_data(p, nid) or {}).values()
                ):
                    raise GraphIntegrityError(f"symbol node {nid} not contained anywhere")
            if ntype == "file" and data.get("rel_path") is None:
                raise GraphIntegrityError(f"file node {nid} missing rel_path")


def build_graph(
    repo_id: str,
    files: list[Any],
    symbols: list[Any],
    relationships: list[Any],
) -> nx.DiGraph:
    """Convenience wrapper used by the pipeline (pre-persistence validation)."""
    return GraphBuilder(repo_id, files, symbols, relationships).build()
