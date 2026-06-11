"""Symbol indices for FQN resolution: built once by IndexBuilder, read-only after."""

import builtins
import os
import sys
from dataclasses import dataclass

from cgis.core.models import SELF_PREFIX, Node, NodeNamespace, NodeType

_BUILTINS: frozenset[str] = frozenset(dir(builtins))


@dataclass(frozen=True)
class SymbolIndex:
    """Immutable lookup indices over the extracted node set.

    Built by IndexBuilder, consumed by SymbolResolver and ResolverEngine.
    Frozen by convention (field rebinding prevented); the contained dicts
    are never mutated after construction.
    """

    # node id (FQN) -> Node
    nodes: dict[str, Node]
    # name -> list of FQNs
    global_symbols: dict[str, list[str]]
    # (file_path, name) -> list of FQNs (list handles conditional redefinitions)
    file_global_symbols: dict[tuple[str, str], list[str]]
    # class_fqn -> {method_name -> method_fqn}
    class_methods: dict[str, dict[str, str]]
    # DI-alias (VARIABLE) indices for raw_dep: resolution; kept separate
    # from global_symbols so call resolution behavior does not change.
    variable_symbols: dict[str, list[str]]
    file_variable_symbols: dict[tuple[str, str], list[str]]
    # normalized file_path -> {local_alias: target_fqn}  (from FILE node import_map)
    file_imports: dict[str, dict[str, str]]
    # suffix_fqn -> [full_node_ids]  (handles src/ layout prefix mismatch)
    suffix_map: dict[str, list[str]]
    # top-level root segments of all internal nodes (for classify)
    internal_roots: set[str]
    # root segments of absolute imports (anything else is UNKNOWN)
    external_roots: set[str]

    def map_to_node_fqn(self, imported_fqn: str) -> str | None:
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
        candidates = self.suffix_map.get(imported_fqn, [])
        if len(candidates) == 1:
            return candidates[0]
        # Strip leading segments from the imported FQN (import has extra prefix)
        parts = imported_fqn.split(".")
        for i in range(1, len(parts)):
            candidate = ".".join(parts[i:])
            if candidate in self.nodes:
                return candidate
        return None

    def classify_fqn(self, fqn: str) -> NodeNamespace:
        """Classify an FQN as STDLIB, INTERNAL, EXTERNAL, or UNKNOWN.

        UNKNOWN means the root segment was not found in internal roots,
        stdlib/builtins, or any known import-map external root.
        """
        if fqn.startswith((".", SELF_PREFIX)):
            return NodeNamespace.INTERNAL
        root = fqn.split(".", maxsplit=1)[0]
        if root in self.internal_roots:
            return NodeNamespace.INTERNAL
        if root in sys.stdlib_module_names or root in _BUILTINS:
            return NodeNamespace.STDLIB
        if root in self.external_roots:
            return NodeNamespace.EXTERNAL
        return NodeNamespace.UNKNOWN

    def is_variable_node(self, fqn: str) -> bool:
        """Return True when fqn names an existing VARIABLE node in the graph."""
        node = self.nodes.get(fqn)
        return node is not None and node.type == NodeType.VARIABLE

    def normalized_file_path(self, source_fqn: str, edge_file_path: str | None) -> str | None:
        """Return the normalized file path for a source FQN, falling back to edge_file_path."""
        source_node = self.nodes.get(source_fqn)
        if source_node:
            return os.path.normpath(source_node.file_path)
        return os.path.normpath(edge_file_path) if edge_file_path else None


class IndexBuilder:
    """Builds a SymbolIndex from extracted nodes (Phase 1: indexing).

    Takes only nodes — never edges. The inheritance tree is deliberately
    NOT built here: resolving EXTENDS targets requires symbol resolution,
    so it belongs to SymbolResolver (spec §2.4).
    """

    def build(self, nodes: list[Node]) -> SymbolIndex:
        """Index all nodes for fast resolution and return the frozen index."""
        nodes_by_id = {n.id: n for n in nodes}
        global_symbols: dict[str, list[str]] = {}
        file_global_symbols: dict[tuple[str, str], list[str]] = {}
        class_methods: dict[str, dict[str, str]] = {}
        variable_symbols: dict[str, list[str]] = {}
        file_variable_symbols: dict[tuple[str, str], list[str]] = {}
        file_imports: dict[str, dict[str, str]] = {}
        suffix_map: dict[str, list[str]] = {}
        internal_roots: set[str] = set()

        for node in nodes_by_id.values():
            # Index FILE-level import maps
            if node.type == NodeType.FILE:
                normalized = os.path.normpath(node.file_path)
                file_imports[normalized] = node.metadata.get("import_map") or {}

            # Index global functions/symbols
            if node.type in (NodeType.FUNCTION, NodeType.CLASS):
                global_symbols.setdefault(node.name, []).append(node.id)
                file_global_symbols.setdefault(
                    (os.path.normpath(node.file_path), node.name), []
                ).append(node.id)

            # Index methods within classes
            if node.type == NodeType.METHOD:
                class_fqn, sep, _ = node.id.rpartition(".")
                if sep:
                    class_methods.setdefault(class_fqn, {})[node.name] = node.id

            # Index DI aliases for raw_dep: candidate resolution
            if node.type == NodeType.VARIABLE:
                variable_symbols.setdefault(node.name, []).append(node.id)
                file_variable_symbols.setdefault(
                    (os.path.normpath(node.file_path), node.name), []
                ).append(node.id)

            # Build suffix map for src/-layout prefix normalization:
            # "src.cgis.pipeline.X" → suffix "cgis.pipeline.X" also points to the node
            self._add_node_to_suffix_map(node.id, suffix_map, internal_roots)

        return SymbolIndex(
            nodes=nodes_by_id,
            global_symbols=global_symbols,
            file_global_symbols=file_global_symbols,
            class_methods=class_methods,
            variable_symbols=variable_symbols,
            file_variable_symbols=file_variable_symbols,
            file_imports=file_imports,
            suffix_map=suffix_map,
            internal_roots=internal_roots,
            external_roots=self._build_external_roots(file_imports),
        )

    def _add_node_to_suffix_map(
        self, node_id: str, suffix_map: dict[str, list[str]], internal_roots: set[str]
    ) -> None:
        """Add node to suffix map and internal roots based on its ID."""
        parts = node_id.split(".")
        internal_roots.add(parts[0])
        if len(parts) > 1 and parts[0] in {"src", "lib"}:
            internal_roots.add(parts[1])
        for i in range(1, len(parts)):
            suffix = ".".join(parts[i:])
            suffix_map.setdefault(suffix, []).append(node_id)

    def _build_external_roots(self, file_imports: dict[str, dict[str, str]]) -> set[str]:
        """Build set of known external root modules from file import maps.

        Any root not in internal_roots, stdlib, or external_roots
        is classified as UNKNOWN by classify_fqn.
        """
        return {
            val.split(".", maxsplit=1)[0]
            for import_map in file_imports.values()
            for val in import_map.values()
            if val and not val.startswith(".")
        }
