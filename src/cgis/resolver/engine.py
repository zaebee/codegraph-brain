"""Implements ResolverEngine class."""

from cgis.core.models import Edge, Node, NodeType


class ResolverEngine:
    """
    The 'Brain' of the CGIS.
    Transforms 'raw' semantic edges into resolved, high-confidence edges.
    """

    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        self.nodes = {n.id: n for n in nodes}
        self.edges = edges
        self.num_parts = 3

        # Indices for fast lookup
        self._global_symbols: dict[str, list[str]] = {}  # name -> list of FQNs
        self._class_methods: dict[
            str, dict[str, str]
        ] = {}  # class_fqn -> {method_name -> method_fqn}

        self._build_indices()

    def _build_indices(self) -> None:
        """Phase 1: Indexing all nodes for fast resolution."""
        for node in self.nodes.values():
            # Index global functions/symbols
            if node.type == NodeType.FUNCTION:
                # We use the name as a key for direct calls, allowing multiple candidates
                self._global_symbols.setdefault(node.name, []).append(node.id)

            # Index methods within classes
            if node.type == NodeType.METHOD:
                # Logic: extract class FQN from method FQN
                # Expected format: "file_path:class_name:method_name"
                parts = node.id.split(":")
                if len(parts) >= self.num_parts:
                    class_fqn = ":".join(parts[:-1])
                    if class_fqn not in self._class_methods:
                        self._class_methods[class_fqn] = {}
                    self._class_methods[class_fqn][node.name] = node.id

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
                new_target = self._resolve_global_call(raw_name, edge.source)

            if new_target and new_target in self.nodes:
                # Create a new resolved edge with higher confidence
                resolved_edges.append(
                    Edge(
                        id=edge.id,
                        source=edge.source,
                        target=new_target,
                        type=edge.type,
                        weight=edge.weight,
                        confidence=min(edge.confidence + 0.5, 1.0),  # Boost confidence
                        context=edge.context,
                        file_path=edge.file_path,
                        line_number=edge.line_number,
                    )
                )
            else:
                # If we couldn't resolve it, keep it as is (or could mark as unresolved)
                resolved_edges.append(edge)

        return resolved_edges

    def _resolve_self_call(self, source_fqn: str, method_name: str) -> str | None:
        """Attempts to find a method on the class that owns the source node."""
        # source_fqn format: "file:class:method"
        parts = source_fqn.split(":")
        if len(parts) < self.num_parts:
            return None

        class_fqn = ":".join(parts[:-1])
        return self._class_methods.get(class_fqn, {}).get(method_name)

    def _resolve_global_call(self, name: str, source_fqn: str) -> str | None:
        """Attempts to find a global function by name, preferring the same file context."""
        candidates = self._global_symbols.get(name, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Try to disambiguate by matching the source file path
        source_node = self.nodes.get(source_fqn)
        if source_node:
            for candidate_id in candidates:
                candidate_node = self.nodes.get(candidate_id)
                if candidate_node and candidate_node.file_path == source_node.file_path:
                    return candidate_id
        return candidates[0]
