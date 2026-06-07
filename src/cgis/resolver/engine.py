"""Implements ResolverEngine class."""

import builtins
import os
import sys

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, Node, NodeNamespace, NodeType

_BUILTINS: frozenset[str] = frozenset(dir(builtins))
_SELF_PREFIX = "self."


class ResolverEngine:
    """
    The 'Brain' of the CGIS.
    Transforms 'raw' semantic edges into resolved, high-confidence edges.
    """

    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        self.nodes = {n.id: n for n in nodes}
        self.edges = edges

        # Indices for fast lookup
        # name -> list of FQNs
        self._global_symbols: dict[str, list[str]] = {}
        # (file_path, name) -> list of FQNs (list handles conditional redefinitions)
        self._file_global_symbols: dict[tuple[str, str], list[str]] = {}
        # class_fqn -> {method_name -> method_fqn}
        self._class_methods: dict[str, dict[str, str]] = {}
        # normalized file_path -> {local_alias: target_fqn}  (from FILE node import_map)
        self._file_imports: dict[str, dict[str, str]] = {}
        # suffix_fqn -> [full_node_ids]  (handles src/ layout prefix mismatch)
        self._suffix_map: dict[str, list[str]] = {}
        # top-level root segments of all internal nodes (for classify)
        self._internal_roots: set[str] = set()

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

            # Build suffix map for src/-layout prefix normalization:
            # "src.cgis.pipeline.X" → suffix "cgis.pipeline.X" also points to the node
            self._add_node_to_suffix_map(node.id)

        self._build_external_roots()

    def _build_external_roots(self) -> None:
        """Build set of known external root modules from file import maps.

        Any root not in internal_roots, stdlib, or external_roots
        is classified as UNKNOWN by _classify_fqn.
        """
        self._external_roots: set[str] = {
            val.split(".", maxsplit=1)[0]
            for import_map in self._file_imports.values()
            for val in import_map.values()
            if val
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
        Resolves raw_call targets to FQNs. Remaining unresolved calls become
        virtual nodes classified as STDLIB or EXTERNAL.

        Returns (resolved_edges, virtual_nodes).
        """
        resolved_edges: list[Edge] = []
        virtual_nodes: dict[str, Node] = {}

        for edge in self.edges:
            if not edge.target.startswith("raw_call:"):
                resolved_edges.append(edge)
                continue

            raw_name = edge.target.removeprefix("raw_call:")
            new_target: str | None = None

            if raw_name.startswith(_SELF_PREFIX):
                method_name = raw_name.removeprefix(_SELF_PREFIX)
                new_target = self._resolve_self_call(edge.source, method_name)
            else:
                new_target = self._resolve_global_call(raw_name, edge.source, edge.file_path)

            if new_target:
                resolved_edges.append(
                    edge.model_copy(
                        update={
                            "target": new_target,
                            "confidence": min(edge.confidence + 0.5, 1.0),
                        }
                    )
                )
            else:
                resolved_edges.append(
                    edge.model_copy(
                        update={
                            "target": raw_name,
                            "confidence": 0.8,
                        }
                    )
                )

        # Create virtual nodes for any edge target not already in internal nodes
        # (covers both unresolved calls and resolved-but-external targets like json.dumps)
        for edge in resolved_edges:
            target = edge.target
            if target not in self.nodes and target not in virtual_nodes:
                ns = self._classify_fqn(target)
                virtual_nodes[target] = self._make_virtual_node(target, ns)

        return resolved_edges, list(virtual_nodes.values())

    def _resolve_self_call(self, source_fqn: str, method_name: str) -> str | None:
        """Attempts to find a method on the class that owns the source node."""
        class_fqn, sep, _ = source_fqn.rpartition(".")
        if not sep:
            return None
        return self._class_methods.get(class_fqn, {}).get(method_name)

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

    def _resolve_global_call(
        self, name: str, source_fqn: str, edge_file_path: str | None = None
    ) -> str | None:
        """Resolve a global call using import map, then global symbol index."""
        source_node = self.nodes.get(source_fqn)
        if source_node:
            file_path: str | None = os.path.normpath(source_node.file_path)
        elif edge_file_path:
            file_path = os.path.normpath(edge_file_path)
        else:
            file_path = None

        if file_path:
            result = self._resolve_via_import_map(name, file_path)
            if result:
                return result

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
