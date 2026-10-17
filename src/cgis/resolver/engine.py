"""Implements ResolverEngine class."""

from collections.abc import Iterable, Mapping

from cgis.core.models import (
    DEFAULT_LANGUAGE,
    RAW_CALL_PREFIX,
    RAW_CLASS_PREFIX,
    SELF_PREFIX,
    VALUE_ARG_CONTEXT,
    VALUE_LOCAL_CONTEXT,
    VIRTUAL_FILE_PATH,
    Edge,
    EdgeType,
    Node,
    NodeNamespace,
    NodeType,
)
from cgis.core.resolution import ResolutionView
from cgis.languages import pack_for_path, rules_for
from cgis.resolver.symbols import SymbolResolver, dispatch_edges

RAW_DEP_PREFIX = "raw_dep:"
RAW_IMPORT_PREFIX = "raw_import:"


#: What an edge into an unresolved boundary scores. The same value an extractor
#: gives a call it could not type, so the two are comparable in one filter.
_UNRESOLVED_CONFIDENCE = 0.8
#: What a call the resolver found a target for scores, whatever language made it (#674).
RESOLVED_CALL_CONFIDENCE = 1.0


_VALUE_CONTEXTS = (VALUE_ARG_CONTEXT, VALUE_LOCAL_CONTEXT)
_CALLABLE = frozenset({NodeType.FUNCTION, NodeType.METHOD})


def _call_target(
    resolver: SymbolResolver, raw_name: str, edge: Edge, site: str | None
) -> str | None:
    """What a raw call name made in `edge`'s source resolves to, or None."""
    if raw_name.startswith(SELF_PREFIX):
        return resolver.resolve_self_call(edge.source, raw_name.removeprefix(SELF_PREFIX))
    return resolver.resolve_global_call(raw_name, edge.source, edge.file_path, site)


def _value_call_edge(resolver: SymbolResolver, view: ResolutionView, edge: Edge) -> Edge | None:
    """A function value the code calls (docs/specs/core/ontology.yml CV-1, CV-2), or None.

    CV-1: argument i of the call at the recorded site counts only when that call
    resolves to an invoker the source's pack lists with position i. CV-2: a local
    holding the value is called where it is held. Either way the value must be a
    real function or method; a name that is not one was never a call.
    """
    context = edge.context or ""
    via = "via local value"
    if context.startswith(VALUE_ARG_CONTEXT):
        position, _, call_site = context.removeprefix(VALUE_ARG_CONTEXT).partition(":")
        invoker = _call_target(resolver, call_site.rpartition("@")[0], edge, call_site)
        rules = rules_for(edge.file_path)  # a value edge always carries its file
        if invoker is None or rules is None or int(position) not in rules.invokers.get(invoker, ()):
            return None
        via = f"via {invoker}"
    raw_name = edge.target.removeprefix(RAW_CALL_PREFIX)
    site = edge.id.partition(RAW_CALL_PREFIX)[2] or None
    target = raw_name if raw_name in view.nodes else _call_target(resolver, raw_name, edge, site)
    node = view.nodes.get(target) if target is not None else None
    if node is None or node.type not in _CALLABLE:
        return None
    return edge.model_copy(
        update={"target": target, "confidence": RESOLVED_CALL_CONFIDENCE, "context": via}
    )


def _unresolved_target(view: ResolutionView, raw_name: str) -> str:
    """What an unresolved call or class reference targets: its bare raw name, unless
    that is the id of a real node, which the edge would join and read as resolved
    (#661). Such a name takes the `raw_call:` prefix, which no real id holds and
    consumers already read as unresolved; a class reference gives up `raw_class:`."""
    return f"{RAW_CALL_PREFIX}{raw_name}" if raw_name in view.nodes else raw_name


def _virtual_rank(node: Node) -> tuple[bool, str]:
    """Which of two readings of one virtual id wins: a known namespace, then the
    language that sorts first — never the order edges happen to arrive in."""
    return (node.namespace is NodeNamespace.UNKNOWN, node.language)


def _pack_name(source_file: str | None) -> str:
    """The language of a file no node speaks for: its pack's name, else the Node default."""
    pack = pack_for_path(source_file) if source_file else None
    return pack.name if pack is not None else DEFAULT_LANGUAGE


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


def _fallback(view: ResolutionView, name: str, file_path: str | None) -> tuple[str, float] | None:
    """The source pack's fallback for a call nothing resolved: (target, confidence), or None.

    No file means no language, so nothing is rewritten — as before step 4.
    """
    if not file_path:
        return None
    rules = rules_for(file_path)
    if rules is None or rules.fallback is None:
        return None
    return rules.fallback(name, file_path, view)


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
        tsconfig_paths: Mapping[str, Mapping[str, list[str]]] | None = None,
        dependency_roots: Iterable[str] = (),
        npm_dependencies: Iterable[str] = (),
        project_state: Mapping[str, object] | None = None,
    ) -> None:
        """Build the symbol resolver (which builds the index) from the extracted graph.

        `workspace_packages` maps dotted package names to directory FQNs, from the
        `package.json` files the pipeline walked, so TypeScript imports of a
        workspace package resolve to its modules (#504). `tsconfig_paths` maps a
        project directory to the `compilerOptions.paths` aliases its files import
        through (#508). `dependency_roots` are the import roots the project's
        manifests declare, which tell a library apart from a package of ours that
        shares its name (#495). `npm_dependencies` are the npm packages the
        `package.json` files declare, as written, which make a TypeScript import EXTERNAL (#556).
        `project_state` holds every other fact a language pack declared (step 7, H3).
        """
        self.edges = edges
        self._resolver = SymbolResolver(
            nodes,
            edges,
            workspace_packages,
            tsconfig_paths,
            dependency_roots,
            npm_dependencies,
            project_state,
        )
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
            elif not edge.target.startswith(RAW_CALL_PREFIX):
                final_edge = self._reconciled_layout_edge(edge)
                resolved_edges.append(final_edge)
                self._ensure_virtual_node(final_edge, virtual_nodes)
            elif (edge.context or "").startswith(_VALUE_CONTEXTS):
                value_edge = _value_call_edge(self._resolver, self._index, edge)
                if value_edge is not None:  # a candidate that is no call leaves no trace
                    resolved_edges.append(value_edge)
            else:
                call_edge = self._resolved_call_edge(edge)
                resolved_edges.append(call_edge)
                self._ensure_virtual_node(call_edge, virtual_nodes)

        nodes = self._index.nodes.values()
        methods = [n for n in nodes if n.type == NodeType.METHOD]
        classes = [n for n in nodes if n.type == NodeType.CLASS]
        resolved_edges.extend(dispatch_edges(methods, self._resolver.inheritance_tree, classes))
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
        final_target = resolved or _unresolved_target(self._index, raw)
        confidence = 1.0 if resolved else 0.5
        return edge.model_copy(update={"target": final_target, "confidence": confidence})

    def _resolved_call_edge(self, edge: Edge) -> Edge:
        """Resolve a raw_call: edge to its final call target FQN.

        Strips the raw_call: prefix, dispatches to resolve_self_call for
        self.* calls or resolve_global_call otherwise, then returns a copy of
        the edge with the resolved target and adjusted confidence:
        1.0 on success, 0.8 on failure, in every language: confidence says how
        the call resolved, not where its extractor started (#674).
        """
        raw_name = edge.target.removeprefix(RAW_CALL_PREFIX)
        if raw_name.startswith(SELF_PREFIX):
            new_target = self._resolver.resolve_self_call(
                edge.source, raw_name.removeprefix(SELF_PREFIX)
            )
        else:
            # `<raw name>@<row>:<col>`: the key a source's `site_types` uses (#643).
            site = edge.id.partition(RAW_CALL_PREFIX)[2] or None
            new_target = self._resolver.resolve_global_call(
                raw_name, edge.source, edge.file_path, site
            )
        if new_target is None:
            # Fallback only: a project symbol named like a global has already won above.
            # Confidence stays at the unresolved 0.8, the value a Python builtin gets.
            file_path = self._index.normalized_file_path(edge.source, edge.file_path)
            hit = _fallback(self._index, raw_name, file_path)
            if hit is not None:
                return edge.model_copy(update={"target": hit[0], "confidence": hit[1]})
        final_target = new_target or _unresolved_target(self._index, raw_name)
        confidence = RESOLVED_CALL_CONFIDENCE if new_target else _UNRESOLVED_CONFIDENCE
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
        The node takes its caller's language (#672); between two callers that agree
        on knownness, the language that sorts first wins, for the same reason.
        """
        target = edge.target
        if self._index.has_node(target):
            return
        existing = virtual_nodes.get(target)
        source_file = self._index.normalized_file_path(edge.source, edge.file_path)
        source = self._index.nodes.get(edge.source)
        language = source.language if source is not None else _pack_name(source_file)
        if existing is not None and _virtual_rank(existing) <= (False, language):
            return  # nothing this caller can classify would outrank it
        # A collision kept its `raw_call:` prefix (#661); the name itself is classified.
        namespace = self._index.classify_fqn(target.removeprefix(RAW_CALL_PREFIX), source_file)
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
        rank = (namespace is NodeNamespace.UNKNOWN, language)
        if existing is None or rank < _virtual_rank(existing):
            virtual_nodes[target] = self._make_virtual_node(target, namespace, language)

    def _make_virtual_node(self, fqn: str, namespace: NodeNamespace, language: str) -> Node:
        """Create a placeholder node for an external/stdlib symbol, in its caller's language."""
        return Node(
            id=fqn,
            type=NodeType.FUNCTION,
            name=fqn.removeprefix(RAW_CALL_PREFIX).rsplit(".", maxsplit=1)[-1],
            file_path=VIRTUAL_FILE_PATH,
            start_line=0,
            end_line=0,
            namespace=namespace,
            language=language,
            confidence_score=0.8,
        )
