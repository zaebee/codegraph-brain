"""Implements ResolverEngine class."""

from collections.abc import Mapping

from cgis.core.models import (
    RAW_CLASS_PREFIX,
    SELF_PREFIX,
    VIRTUAL_FILE_PATH,
    Edge,
    EdgeType,
    Node,
    NodeNamespace,
    NodeType,
)
from cgis.resolver.js_builtins import js_builtin_target
from cgis.resolver.symbols import SymbolResolver

RAW_DEP_PREFIX = "raw_dep:"
RAW_IMPORT_PREFIX = "raw_import:"


#: What an edge into an unresolved boundary scores. The same value an extractor
#: gives a call it could not type, so the two are comparable in one filter.
_UNRESOLVED_CONFIDENCE = 0.8


def _agree_on_confidence(edges: list[Edge], virtual_nodes: dict[str, Node]) -> list[Edge]:
    """Cap an edge at the unresolved score when its target is an UNKNOWN boundary node.

    The import map raises confidence to 1.0 whenever it produced a target, which
    left 3,077 IMPORTS and 1,219 CALLS edges on one backend at full confidence
    pointing at a node the graph itself labels unresolved. `--min-confidence 0.9`
    then dropped the honest 0.8 calls and kept those — the filter inverted (#493
    review). Node and edge now say the same thing.
    """
    unresolved = {
        node_id
        for node_id, node in virtual_nodes.items()
        if node.namespace is NodeNamespace.UNKNOWN
    }
    return [
        edge.model_copy(update={"confidence": _UNRESOLVED_CONFIDENCE})
        if edge.target in unresolved and edge.confidence > _UNRESOLVED_CONFIDENCE
        else edge
        for edge in edges
    ]


class ResolverEngine:
    """
    The 'Brain' of the CGIS.
    Transforms 'raw' semantic edges into resolved, high-confidence edges.

    Thin facade: SymbolResolver builds the SymbolIndex internally (via
    IndexBuilder), maps raw names to FQNs, and this class keeps edge
    finalization — confidence policy, edge rewrites, and virtual-node
    creation (spec §2.5).
    """

    def __init__(
        self,
        nodes: list[Node],
        edges: list[Edge],
        *,
        workspace_packages: Mapping[str, str] | None = None,
    ) -> None:
        """Build the symbol resolver (which builds the index) from the extracted graph.

        `workspace_packages` maps dotted package names to directory FQNs, from the
        `package.json` files the pipeline walked, so TypeScript imports of a
        workspace package resolve to its modules (#504).
        """
        self.edges = edges
        self._resolver = SymbolResolver(nodes, edges, workspace_packages)
        self._index = self._resolver.index

    def resolve(self) -> tuple[list[Edge], list[Node]]:
        """
        Phase 3: The Linking Pass.
        Resolves raw_call targets to FQNs in a single pass. Virtual nodes for
        boundary symbols (STDLIB/EXTERNAL/UNKNOWN) are created on the fly as
        each edge target is finalized.

        Returns (resolved_edges, virtual_nodes).
        """
        resolved_edges: list[Edge] = []
        virtual_nodes: dict[str, Node] = {}

        for edge in self.edges:
            if edge.target.startswith(RAW_CLASS_PREFIX):
                class_edge = self._resolved_class_edge(edge)
                resolved_edges.append(class_edge)
                self._ensure_virtual_node(class_edge, virtual_nodes)
            elif edge.target.startswith(RAW_DEP_PREFIX):
                dep_edge = self._resolved_dep_edge(edge)
                if dep_edge is not None:
                    resolved_edges.append(dep_edge)
            elif edge.target.startswith(RAW_IMPORT_PREFIX):
                import_edge = self._resolved_import_edge(edge)
                if import_edge is not None:
                    resolved_edges.append(import_edge)
                # no _ensure_virtual_node: target exists on hit, edge dies on miss
            elif not edge.target.startswith("raw_call:"):
                final_edge = self._reconciled_layout_edge(edge)
                resolved_edges.append(final_edge)
                self._ensure_virtual_node(final_edge, virtual_nodes)
            else:
                call_edge = self._resolved_call_edge(edge)
                resolved_edges.append(call_edge)
                self._ensure_virtual_node(call_edge, virtual_nodes)

        return _agree_on_confidence(resolved_edges, virtual_nodes), list(virtual_nodes.values())

    def _reconciled_layout_edge(self, edge: Edge) -> Edge:
        """Point an already-final target at the node it names, prefix differences aside.

        Call targets go through `SymbolResolver`, which reconciles layout; a module
        import never did, so `from app.models import X` pointed at `app.models`
        while the graph held `models` (#494). Confidence is untouched: this is the
        same name, spelled the way the node ids spell it, and only in the language
        whose imports taught the prefix.
        """
        source_file = self._index.normalized_file_path(edge.source, edge.file_path)
        resolved = self._index.resolve_import_target(edge.target, source_file)
        if resolved is None or resolved == edge.target:
            return edge
        return edge.model_copy(update={"target": resolved})

    def _resolved_class_edge(self, edge: Edge) -> Edge:
        """Resolve a raw_class: edge to its final class FQN.

        Strips the raw_class: prefix, resolves via SymbolResolver, then
        returns a copy of the edge with the resolved target (confidence 1.0) or
        the bare name as fallback (confidence 0.5).
        """
        raw = edge.target.removeprefix(RAW_CLASS_PREFIX)
        resolved = self._resolver.resolve_class_ref(raw, edge.source, edge.file_path)
        final_target = resolved or raw
        confidence = 1.0 if resolved else 0.5
        return edge.model_copy(update={"target": final_target, "confidence": confidence})

    def _resolved_call_edge(self, edge: Edge) -> Edge:
        """Resolve a raw_call: edge to its final call target FQN.

        Strips the raw_call: prefix, dispatches to resolve_self_call for
        self.* calls or resolve_global_call otherwise, then returns a copy of
        the edge with the resolved target and adjusted confidence:
        min(edge.confidence + 0.5, 1.0) on success, 0.8 on failure.
        """
        raw_name = edge.target.removeprefix("raw_call:")
        if raw_name.startswith(SELF_PREFIX):
            new_target = self._resolver.resolve_self_call(
                edge.source, raw_name.removeprefix(SELF_PREFIX)
            )
        else:
            new_target = self._resolver.resolve_global_call(raw_name, edge.source, edge.file_path)
        if new_target is None:
            # Fallback only: a project symbol named like a global has already won above.
            # Confidence stays at the unresolved 0.8, the value a Python builtin gets.
            file_path = self._index.normalized_file_path(edge.source, edge.file_path)
            shadowed = self._index.file_shadowed_globals.get(file_path or "", frozenset())
            new_target = js_builtin_target(raw_name, file_path, shadowed)
            if new_target is not None:
                return edge.model_copy(update={"target": new_target, "confidence": 0.8})
        final_target = new_target or raw_name
        confidence = min(edge.confidence + 0.5, 1.0) if new_target else 0.8
        return edge.model_copy(update={"target": final_target, "confidence": confidence})

    def _resolved_dep_edge(self, edge: Edge) -> Edge | None:
        """Resolve a raw_dep: candidate edge, or None when it must be dropped (spec §3.3).

        A candidate resolves in one of two ways: to a DI alias (a VARIABLE node),
        which keeps it a DEPENDS_ON wiring edge, or to an internal class, which
        makes it a REFERENCES annotation edge (spec D4). Anything else is a
        speculative candidate that must not leak into the output.

        The internal-class check is inlined here rather than delegated to
        SymbolResolver: resolution (resolve_class_ref, already public and
        already called from this class) belongs to the symbol layer, but "is
        this FQN an existing CLASS node" is edge-finalization policy — this
        class's stated job — not a resolution strategy. resolve_class_ref can
        return an import-map FQN for a symbol with no node (a third-party
        type via its `... or target_fqn` fallback); the membership check
        below is what keeps that out (spec D3).

        A candidate whose source is the target class itself, or lives inside
        it (a method, or a nested class), is also dropped: a class naming
        itself is not evidence anyone uses it, and counting it would
        manufacture a false negative in the orphan query (spec D9).
        """
        dep_name = edge.target.removeprefix(RAW_DEP_PREFIX)
        dep_target = self._resolver.resolve_dep_candidate(dep_name, edge.source, edge.file_path)
        if dep_target is not None:
            return edge.model_copy(update={"target": dep_target, "confidence": 1.0})
        resolved = self._resolver.resolve_class_ref(dep_name, edge.source, edge.file_path)
        if resolved is not None:
            class_node = self._index.nodes.get(resolved)
            if class_node is not None and class_node.type == NodeType.CLASS:
                if edge.source == resolved or edge.source.startswith(f"{resolved}."):
                    return None
                return edge.model_copy(
                    update={"target": resolved, "type": EdgeType.REFERENCES, "confidence": 1.0}
                )
        return None

    def _resolved_import_edge(self, edge: Edge) -> Edge | None:
        """Resolve a raw_import: symbol edge, or None when it must be dropped.

        Reuses SymbolIndex.map_to_node_fqn (exact / suffix / strip-prefix). An
        external or unknown symbol drops the edge: the module-level IMPORTS
        edge already captures the coupling — raw_import: never leaks into
        output and never mints a virtual node (spec §2.2/§2.4).
        """
        imported_fqn = edge.target.removeprefix(RAW_IMPORT_PREFIX)
        node_fqn = self._index.map_to_node_fqn(imported_fqn)
        if node_fqn is None:
            return None
        return edge.model_copy(update={"target": node_fqn, "confidence": 1.0})

    def _ensure_virtual_node(self, edge: Edge, virtual_nodes: dict[str, Node]) -> None:
        """Create a virtual boundary node for the edge's target if the graph lacks one.

        Classified in the language of the edge's source file (#454). The node id
        is shared, so a target string reached from both languages — `json.dumps`
        from a Python module and from a TS local named `json` — needs one answer.
        A known namespace beats UNKNOWN, whichever edge comes first: the Python
        reading is a claim about the symbol, the TS one only says "not known", and
        file walk order is not sorted, so first-seen would differ across machines.
        """
        target = edge.target
        if self._index.has_node(target):
            return
        existing = virtual_nodes.get(target)
        if existing is not None and existing.namespace != NodeNamespace.UNKNOWN:
            return
        source_file = self._index.normalized_file_path(edge.source, edge.file_path)
        namespace = self._index.classify_fqn(target, source_file)
        if namespace is NodeNamespace.INTERNAL:
            # An internal-root FQN with no node is one of four things, measured on
            # owner-api's 681 such nodes (#459, and the review of #493):
            #   * a module the graph holds under a different spelling — 59% of the
            #     edges: ingesting `app/` strips the `app.` the imports carry, so
            #     `app.models` is in the graph as `models`. The import path does not
            #     run through `resolve_layout`, which exists for exactly this;
            #   * an unresolved receiver whose variable name collides with an
            #     internal package (`storage: OpenSearchClientDep` → `storage.read`);
            #   * a third-party symbol under a colliding root — `from alembic import
            #     op`, or SQLAlchemy on a FastAPI instance named `app`;
            #   * a symbol that is genuinely absent, a rename that half-landed.
            # None of them is a *resolved internal call*, so UNKNOWN is the honest
            # floor for all four, and `get_edge_stats` counts them unresolved —
            # classifying them INTERNAL was the same masking as #414 and #454. The
            # target is kept, so the name still shows up in `top_unresolved`.
            namespace = NodeNamespace.UNKNOWN
        if existing is None or namespace != NodeNamespace.UNKNOWN:
            virtual_nodes[target] = self._make_virtual_node(target, namespace)

    def _make_virtual_node(self, fqn: str, namespace: NodeNamespace) -> Node:
        """Create a placeholder node for an external/stdlib symbol."""
        return Node(
            id=fqn,
            type=NodeType.FUNCTION,
            name=fqn.rsplit(".", maxsplit=1)[-1],
            file_path=VIRTUAL_FILE_PATH,
            start_line=0,
            end_line=0,
            namespace=namespace,
            confidence_score=0.8,
        )
