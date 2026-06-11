"""Implements ResolverEngine class."""

import builtins
import os
import sys

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeNamespace, NodeType

_BUILTINS: frozenset[str] = frozenset(dir(builtins))
_SELF_PREFIX = "self."
_RAW_CLASS_PREFIX = "raw_class:"
RAW_DEP_PREFIX = "raw_dep:"


class ResolverEngine:
    """
    The 'Brain' of the CGIS.
    Transforms 'raw' semantic edges into resolved, high-confidence edges.
    """

    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        """Build resolution indexes from the extracted node and edge lists."""
        self.nodes = {n.id: n for n in nodes}
        self.edges = edges

        # Indices for fast lookup
        # name -> list of FQNs
        self._global_symbols: dict[str, list[str]] = {}
        # (file_path, name) -> list of FQNs (list handles conditional redefinitions)
        self._file_global_symbols: dict[tuple[str, str], list[str]] = {}
        # class_fqn -> {method_name -> method_fqn}
        self._class_methods: dict[str, dict[str, str]] = {}
        # DI-alias (VARIABLE) indices for raw_dep: resolution; kept separate
        # from _global_symbols so call resolution behavior does not change.
        self._variable_symbols: dict[str, list[str]] = {}
        self._file_variable_symbols: dict[tuple[str, str], list[str]] = {}
        # normalized file_path -> {local_alias: target_fqn}  (from FILE node import_map)
        self._file_imports: dict[str, dict[str, str]] = {}
        # suffix_fqn -> [full_node_ids]  (handles src/ layout prefix mismatch)
        self._suffix_map: dict[str, list[str]] = {}
        # top-level root segments of all internal nodes (for classify)
        self._internal_roots: set[str] = set()
        # class_fqn -> [resolved parent FQNs] built from EXTENDS edges
        self._inheritance_tree: dict[str, list[str]] = {}

        self._build_indices()

    def _build_indices(self) -> None:
        """Phase 1: Indexing all nodes for fast resolution."""
        for node in self.nodes.values():
            # Index FILE-level import maps
            if node.type == NodeType.FILE:
                normalized = os.path.normpath(node.file_path)
                self._file_imports[normalized] = node.metadata.get("import_map") or {}

            # Index global functions/symbols
            if node.type in (NodeType.FUNCTION, NodeType.CLASS):
                self._global_symbols.setdefault(node.name, []).append(node.id)
                self._file_global_symbols.setdefault(
                    (os.path.normpath(node.file_path), node.name), []
                ).append(node.id)

            # Index methods within classes
            if node.type == NodeType.METHOD:
                class_fqn, sep, _ = node.id.rpartition(".")
                if sep:
                    self._class_methods.setdefault(class_fqn, {})[node.name] = node.id

            # Index DI aliases for raw_dep: candidate resolution
            if node.type == NodeType.VARIABLE:
                self._variable_symbols.setdefault(node.name, []).append(node.id)
                self._file_variable_symbols.setdefault(
                    (os.path.normpath(node.file_path), node.name), []
                ).append(node.id)

            # Build suffix map for src/-layout prefix normalization:
            # "src.cgis.pipeline.X" → suffix "cgis.pipeline.X" also points to the node
            self._add_node_to_suffix_map(node.id)

        self._build_external_roots()
        self._build_inheritance_tree()

    def _build_inheritance_tree(self) -> None:
        """Build class→[parent FQNs] index from EXTENDS edges for hierarchy traversal."""
        for edge in self.edges:
            if edge.type == EdgeType.EXTENDS:
                raw = edge.target.removeprefix(_RAW_CLASS_PREFIX)
                resolved = self._resolve_class_ref(raw, edge.source, edge.file_path)
                self._inheritance_tree.setdefault(edge.source, []).append(resolved or raw)

    def _resolve_class_ref(
        self, name: str, source_fqn: str, edge_file_path: str | None
    ) -> str | None:
        """Resolve a class name from an EXTENDS edge target to a graph FQN."""
        file_path = self._get_normalized_file_path(source_fqn, edge_file_path)
        if file_path:
            result = self._resolve_via_import_map(name, file_path)
            if result:
                return result
        # Global symbol index is keyed by short name; for dotted refs like `models.BaseModel`
        # strip the module prefix and verify the resolved FQN ends with the full dotted name.
        query_name = name.rsplit(".", maxsplit=1)[-1]
        resolved = self._resolve_via_global_symbols(query_name, file_path)
        if resolved and ("." not in name or resolved == name or resolved.endswith(f".{name}")):
            return resolved
        return None

    def _build_external_roots(self) -> None:
        """Build set of known external root modules from file import maps.

        Any root not in internal_roots, stdlib, or external_roots
        is classified as UNKNOWN by _classify_fqn.
        """
        self._external_roots: set[str] = {
            val.split(".", maxsplit=1)[0]
            for import_map in self._file_imports.values()
            for val in import_map.values()
            if val and not val.startswith(".")
        }

    def _add_node_to_suffix_map(self, node_id: str) -> None:
        """Add node to suffix map and internal roots based on its ID."""
        parts = node_id.split(".")
        self._internal_roots.add(parts[0])
        if len(parts) > 1 and parts[0] in {"src", "lib"}:
            self._internal_roots.add(parts[1])
        for i in range(1, len(parts)):
            suffix = ".".join(parts[i:])
            self._suffix_map.setdefault(suffix, []).append(node_id)

    def _classify_fqn(self, fqn: str) -> NodeNamespace:
        """Classify an FQN as STDLIB, INTERNAL, EXTERNAL, or UNKNOWN.

        UNKNOWN means the root segment was not found in internal roots,
        stdlib/builtins, or any known import-map external root.
        """
        if fqn.startswith((".", _SELF_PREFIX)):
            return NodeNamespace.INTERNAL
        root = fqn.split(".", maxsplit=1)[0]
        if root in self._internal_roots:
            return NodeNamespace.INTERNAL
        if root in sys.stdlib_module_names or root in _BUILTINS:
            return NodeNamespace.STDLIB
        if root in self._external_roots:
            return NodeNamespace.EXTERNAL
        return NodeNamespace.UNKNOWN

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
            if edge.target.startswith(_RAW_CLASS_PREFIX):
                class_edge = self._resolved_class_edge(edge)
                resolved_edges.append(class_edge)
                self._ensure_virtual_node(class_edge.target, virtual_nodes)
            elif edge.target.startswith(RAW_DEP_PREFIX):
                dep_edge = self._resolved_dep_edge(edge)
                if dep_edge is not None:
                    resolved_edges.append(dep_edge)
            elif not edge.target.startswith("raw_call:"):
                resolved_edges.append(edge)
                self._ensure_virtual_node(edge.target, virtual_nodes)
            else:
                call_edge = self._resolved_call_edge(edge)
                resolved_edges.append(call_edge)
                self._ensure_virtual_node(call_edge.target, virtual_nodes)

        return resolved_edges, list(virtual_nodes.values())

    def _resolved_class_edge(self, edge: Edge) -> Edge:
        """Resolve a raw_class: edge to its final class FQN.

        Strips the raw_class: prefix, resolves via _resolve_class_ref, then
        returns a copy of the edge with the resolved target (confidence 1.0) or
        the bare name as fallback (confidence 0.5).
        """
        raw = edge.target.removeprefix(_RAW_CLASS_PREFIX)
        resolved = self._resolve_class_ref(raw, edge.source, edge.file_path)
        final_target = resolved or raw
        confidence = 1.0 if resolved else 0.5
        return edge.model_copy(update={"target": final_target, "confidence": confidence})

    def _resolved_call_edge(self, edge: Edge) -> Edge:
        """Resolve a raw_call: edge to its final call target FQN.

        Strips the raw_call: prefix, dispatches to _resolve_self_call for
        self.* calls or _resolve_global_call otherwise, then returns a copy of
        the edge with the resolved target and adjusted confidence:
        min(edge.confidence + 0.5, 1.0) on success, 0.8 on failure.
        """
        raw_name = edge.target.removeprefix("raw_call:")
        if raw_name.startswith(_SELF_PREFIX):
            new_target = self._resolve_self_call(edge.source, raw_name.removeprefix(_SELF_PREFIX))
        else:
            new_target = self._resolve_global_call(raw_name, edge.source, edge.file_path)
        final_target = new_target or raw_name
        confidence = min(edge.confidence + 0.5, 1.0) if new_target else 0.8
        return edge.model_copy(update={"target": final_target, "confidence": confidence})

    def _ensure_virtual_node(self, target: str, virtual_nodes: dict[str, Node]) -> None:
        """Create a virtual boundary node for target if it is not already in the graph."""
        if target not in self.nodes and target not in virtual_nodes:
            virtual_nodes[target] = self._make_virtual_node(target, self._classify_fqn(target))

    def _resolve_self_call(self, source_fqn: str, method_name: str) -> str | None:
        """Attempts to find a method on the class that owns source, traversing inheritance.

        Walks up the FQN segments to handle nested functions (e.g. mod.Cls.method.inner).
        """
        parts = source_fqn.split(".")
        for i in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in self._class_methods:
                return self._resolve_method_on_class_hierarchy(candidate, method_name, set())
        return None

    def _resolve_method_on_class_hierarchy(
        self, class_fqn: str, method_name: str, visited: set[str]
    ) -> str | None:
        """DFS over EXTENDS edges to find method_name defined in class_fqn or any ancestor."""
        if class_fqn in visited:
            return None
        visited.add(class_fqn)
        direct = self._class_methods.get(class_fqn, {}).get(method_name)
        if direct:
            return direct
        for parent_fqn in self._inheritance_tree.get(class_fqn, []):
            result = self._resolve_method_on_class_hierarchy(parent_fqn, method_name, visited)
            if result:
                return result
        return None

    def _map_to_node_fqn(self, imported_fqn: str) -> str | None:
        """Resolve an imported FQN to an actual node in the graph.

        Handles two common prefix-mismatch patterns:
        - Import has extra package prefix: `cgis.resolver.engine.X` → node `resolver.engine.X`
          (strip leading segments from the imported FQN)
        - Node has extra layout prefix: `cgis.pipeline.X` → node `src.cgis.pipeline.X`
          (look up in suffix_map built from node IDs)

        Returns None when the target is ambiguous or not in the graph.
        """
        if imported_fqn in self.nodes:
            return imported_fqn
        # Node has extra layout prefix (e.g. src/) — most precise match first
        candidates = self._suffix_map.get(imported_fqn, [])
        if len(candidates) == 1:
            return candidates[0]
        # Strip leading segments from the imported FQN (import has extra prefix)
        parts = imported_fqn.split(".")
        for i in range(1, len(parts)):
            candidate = ".".join(parts[i:])
            if candidate in self.nodes:
                return candidate
        return None

    def _resolve_via_import_map(self, name: str, file_path: str) -> str | None:
        """Look up name in the file's import map (direct and module-prefixed calls)."""
        file_import_map = self._file_imports.get(file_path, {})

        if name in file_import_map:
            target_fqn = file_import_map[name]
            return self._map_to_node_fqn(target_fqn) or target_fqn

        first_part = name.split(".", maxsplit=1)[0]
        if first_part in file_import_map and "." in name:
            rest = name[len(first_part) + 1 :]
            target_fqn = f"{file_import_map[first_part]}.{rest}"
            return self._map_to_node_fqn(target_fqn) or target_fqn

        return None

    def _resolve_local_type_call(self, name: str, source_node: Node) -> str | None:
        """Resolve `var.method` using local_types metadata on the source node."""
        if "." not in name:
            return None
        var_name, method_name = name.split(".", maxsplit=1)
        local_types: dict[str, str] = source_node.metadata.get("local_types") or {}
        class_fqn = local_types.get(var_name)
        if not class_fqn:
            return None
        candidate = f"{class_fqn}.{method_name}"
        return self._map_to_node_fqn(candidate) or candidate

    def _get_normalized_file_path(self, source_fqn: str, edge_file_path: str | None) -> str | None:
        """Return the normalized file path for a source FQN, falling back to edge_file_path."""
        source_node = self.nodes.get(source_fqn)
        if source_node:
            return os.path.normpath(source_node.file_path)
        return os.path.normpath(edge_file_path) if edge_file_path else None

    def _resolve_global_call(
        self, name: str, source_fqn: str, edge_file_path: str | None = None
    ) -> str | None:
        """Resolve a global call using local types, import map, then global symbol index."""
        source_node = self.nodes.get(source_fqn)
        file_path = self._get_normalized_file_path(source_fqn, edge_file_path)

        if source_node:
            result = self._resolve_local_type_call(name, source_node)
            if result:
                return result

        if file_path:
            result = self._resolve_via_import_map(name, file_path)
            if result:
                return result

        return self._resolve_via_global_symbols(name, file_path)

    def _resolved_dep_edge(self, edge: Edge) -> Edge | None:
        """Resolve a raw_dep: candidate edge, or None when it must be dropped (spec §3.3)."""
        dep_name = edge.target.removeprefix(RAW_DEP_PREFIX)
        dep_target = self._resolve_dep_candidate(dep_name, edge.source, edge.file_path)
        if dep_target is None:
            # Speculative candidate that is not a DI alias: drop the edge
            # entirely — raw_dep: must never leak into output (spec §3.3).
            return None
        return edge.model_copy(update={"target": dep_target, "confidence": 1.0})

    def _resolve_dep_candidate(
        self, name: str, source_fqn: str, edge_file_path: str | None
    ) -> str | None:
        """Resolve a raw_dep: candidate to a VARIABLE (DI alias) node, or None.

        Order: the consuming file's import map first, then the VARIABLE-only
        symbol index with same-file preference. Returns None for anything that
        is not an existing VARIABLE node — the caller drops the edge.
        A globally-unique match is accepted even when the name is not importable
        in the consuming file — matching _resolve_global_call's behavior.

        When ``name`` is present in the file's import map, the import is
        authoritative: return the hit only if it is a VARIABLE node, otherwise
        return ``None`` immediately — never fall through to the global
        ``_variable_symbols`` index.  This prevents an explicitly imported
        class from being shadowed by a same-named DI alias in another module.
        """
        file_path = self._get_normalized_file_path(source_fqn, edge_file_path)
        if file_path:
            via_import = self._resolve_via_import_map(name, file_path)
            if via_import:
                return via_import if self._is_variable_node(via_import) else None
        candidates = self._variable_symbols.get(name, [])
        if len(candidates) == 1:
            return candidates[0]
        if candidates and file_path:
            same_file = self._file_variable_symbols.get((file_path, name), [])
            if len(same_file) == 1:
                return same_file[0]
        return None

    def _is_variable_node(self, fqn: str) -> bool:
        """Return True when fqn names an existing VARIABLE node in the graph."""
        node = self.nodes.get(fqn)
        return node is not None and node.type == NodeType.VARIABLE

    def _resolve_via_global_symbols(self, name: str, file_path: str | None) -> str | None:
        """Look up name in the global symbol index, preferring same-file candidates."""
        candidates = self._global_symbols.get(name, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if file_path:
            same_file = self._file_global_symbols.get((file_path, name), [])
            if len(same_file) == 1:
                return same_file[0]
        return None
