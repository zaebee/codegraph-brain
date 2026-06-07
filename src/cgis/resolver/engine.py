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
        # (file_path, name) -> list of FQNs (list handles conditional redefinitions)
        self._file_global_symbols: dict[tuple[str, str], list[str]] = {}
        # class_fqn -> {method_name -> method_fqn}
        self._class_methods: dict[str, dict[str, str]] = {}
        # normalized file_path -> {local_alias: target_fqn}  (from FILE node import_map)
        self._file_imports: dict[str, dict[str, str]] = {}
        # suffix_fqn -> [full_node_ids]  (handles src/ layout prefix mismatch)
        self._suffix_map: dict[str, list[str]] = {}

        self._build_indices()

    def _build_indices(self) -> None:
        """Phase 1: Indexing all nodes for fast resolution."""
        for node in self.nodes.values():
            # Index FILE-level import maps
            if node.type == NodeType.FILE:
                normalized = os.path.normpath(node.file_path)
                self._file_imports[normalized] = node.metadata.get("import_map", {})

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
            parts = node.id.split(".")
            for i in range(1, len(parts)):
                suffix = ".".join(parts[i:])
                self._suffix_map.setdefault(suffix, []).append(node.id)

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

            raw_name = edge.target.removeprefix("raw_call:")

            new_target: str | None = None

            if raw_name.startswith("self."):
                method_name = raw_name.removeprefix("self.")
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
                resolved_edges.append(edge)

        return resolved_edges

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
        # Strip leading segments from the imported FQN (import has extra prefix)
        parts = imported_fqn.split(".")
        for i in range(1, len(parts)):
            candidate = ".".join(parts[i:])
            if candidate in self.nodes:
                return candidate
        # Node has extra layout prefix (e.g. src/) — look up suffix_map
        candidates = self._suffix_map.get(imported_fqn, [])
        return candidates[0] if len(candidates) == 1 else None

    def _resolve_global_call(
        self, name: str, source_fqn: str, edge_file_path: str | None = None
    ) -> str | None:
        """Resolve a global call using import map, then global symbol index."""
        source_node = self.nodes.get(source_fqn)
        file_path = os.path.normpath(source_node.file_path) if source_node else (
            os.path.normpath(edge_file_path) if edge_file_path else None
        )

        # 1. Check file-level import map (highest priority — explicit import wins)
        if file_path:
            file_import_map = self._file_imports.get(file_path, {})

            # Direct import: `from X import func` → `func()`
            if name in file_import_map:
                target_fqn = file_import_map[name]
                return self._map_to_node_fqn(target_fqn) or target_fqn

            # Module-prefixed call: `import mod` → `mod.func()`
            first_part = name.split(".")[0]
            if first_part in file_import_map and "." in name:
                rest = name[len(first_part) + 1:]
                resolved_mod = file_import_map[first_part]
                target_fqn = f"{resolved_mod}.{rest}"
                return self._map_to_node_fqn(target_fqn) or target_fqn

        # 2. Global symbol index (same-file preference for disambiguation)
        candidates = self._global_symbols.get(name, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if file_path:
            same_file_candidates = self._file_global_symbols.get(
                (file_path, name), []
            )
            if len(same_file_candidates) == 1:
                return same_file_candidates[0]
        return None
