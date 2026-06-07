"""Implements ResolverEngine class."""

import os

from cgis.core.models import Edge, Node, NodeType


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
        # (file_path, name) -> FQN
        self._file_global_symbols: dict[tuple[str, str], str] = {}
        # class_fqn -> {method_name -> method_fqn}
        self._class_methods: dict[str, dict[str, str]] = {}

        self._build_indices()

    def _build_indices(self) -> None:
        """Phase 1: Indexing all nodes for fast resolution."""
        for node in self.nodes.values():
            # Index global functions/symbols
            if node.type in (NodeType.FUNCTION, NodeType.CLASS):
                # We use the name as a key for direct calls, allowing multiple candidates
                self._global_symbols.setdefault(node.name, []).append(node.id)
                self._file_global_symbols[(os.path.normpath(node.file_path), node.name)] = node.id

            # Index methods within classes
            if node.type == NodeType.METHOD:
                # FQN format: module.ClassName.method_name
                class_fqn, sep, _ = node.id.rpartition(".")
                if sep:
                    self._class_methods.setdefault(class_fqn, {})[node.name] = node.id

    def resolve(self) -> list[Edge]:
        """
        Phase 3: The Linking Pass.
        Iterates through edges and resolves raw_call targets.
        """
        resolved_edges: list[Edge] = []

        for edge in self.edges:
            if not edge.target.startswith("raw_call:"):
                resolved_edges.append(edge)
                continue

            # Extract the name after 'raw_call:'
            raw_name = edge.target.removeprefix("raw_call:")

            new_target: str | None = None

            # Case 1: Handle 'self.method_name'
            if raw_name.startswith("self."):
                method_name = raw_name.removeprefix("self.")
                new_target = self._resolve_self_call(edge.source, method_name)

            # Case 2: Handle direct global call 'func_name'
            else:
                new_target = self._resolve_global_call(raw_name, edge.source, edge.file_path)

            if new_target:
                # Create a new resolved edge with higher confidence
                resolved_edges.append(
                    edge.model_copy(
                        update={
                            "target": new_target,
                            "confidence": min(edge.confidence + 0.5, 1.0),
                        }
                    )
                )
            else:
                # If we couldn't resolve it, keep it as is (or could mark as unresolved)
                resolved_edges.append(edge)

        return resolved_edges

    def _resolve_self_call(self, source_fqn: str, method_name: str) -> str | None:
        """Attempts to find a method on the class that owns the source node."""
        # source_fqn format: module.ClassName.method_name → class_fqn: module.ClassName
        class_fqn, sep, _ = source_fqn.rpartition(".")
        if not sep:
            return None
        return self._class_methods.get(class_fqn, {}).get(method_name)

    def _resolve_global_call(
        self, name: str, source_fqn: str, edge_file_path: str | None = None
    ) -> str | None:
        """Attempts to find a global function by name, preferring the same file context."""
        candidates = self._global_symbols.get(name, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Try to disambiguate by matching the source file path
        source_node = self.nodes.get(source_fqn)
        file_path = source_node.file_path if source_node else edge_file_path
        if file_path:
            same_file_candidate = self._file_global_symbols.get((os.path.normpath(file_path), name))
            if same_file_candidate:
                return same_file_candidate
        return None
