"""Symbol resolution strategies over a SymbolIndex."""

from cgis.core.models import Edge, EdgeType, Node

# Re-exported via ``as``-form so ResolverEngine imports only *this* module,
# keeping the resolver domain's IMPORTS layer a 021C chain
# (engine → symbols → indices) instead of a 030T triangle, per spec §3.3.
# ``noqa: PLC0414`` suppresses pylint's "alias does not rename" — the
# redundant alias is intentional: mypy strict's no_implicit_reexport
# requires it.
from cgis.resolver.indices import _RAW_CLASS_PREFIX as _RAW_CLASS_PREFIX  # noqa: PLC0414
from cgis.resolver.indices import _SELF_PREFIX as _SELF_PREFIX  # noqa: PLC0414
from cgis.resolver.indices import IndexBuilder as IndexBuilder  # noqa: PLC0414
from cgis.resolver.indices import SymbolIndex


class SymbolResolver:
    """Maps raw symbol names to graph FQNs.

    Strategy chain per call site: local variable types, the consuming file's
    import map, then the global symbol index with same-file preference.
    Holds the inheritance tree (a resolution product built from EXTENDS
    edges — not an index, see spec §2.4).
    """

    def __init__(self, index: SymbolIndex, edges: list[Edge]) -> None:
        """Store the index and build the class→parents tree from EXTENDS edges."""
        self._index = index
        # class_fqn -> [resolved parent FQNs] built from EXTENDS edges
        self._inheritance_tree: dict[str, list[str]] = {}
        for edge in edges:
            if edge.type == EdgeType.EXTENDS:
                raw = edge.target.removeprefix(_RAW_CLASS_PREFIX)
                resolved = self.resolve_class_ref(raw, edge.source, edge.file_path)
                self._inheritance_tree.setdefault(edge.source, []).append(resolved or raw)

    def resolve_class_ref(
        self, name: str, source_fqn: str, edge_file_path: str | None
    ) -> str | None:
        """Resolve a class name from an EXTENDS edge target to a graph FQN."""
        file_path = self._index.normalized_file_path(source_fqn, edge_file_path)
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

    def resolve_self_call(self, source_fqn: str, method_name: str) -> str | None:
        """Attempts to find a method on the class that owns source, traversing inheritance.

        Walks up the FQN segments to handle nested functions (e.g. mod.Cls.method.inner).
        """
        parts = source_fqn.split(".")
        for i in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in self._index.class_methods:
                return self._resolve_method_on_class_hierarchy(candidate, method_name, set())
        return None

    def resolve_global_call(
        self, name: str, source_fqn: str, edge_file_path: str | None = None
    ) -> str | None:
        """Resolve a global call using local types, import map, then global symbol index."""
        source_node = self._index.nodes.get(source_fqn)
        file_path = self._index.normalized_file_path(source_fqn, edge_file_path)

        if source_node:
            result = self._resolve_local_type_call(name, source_node)
            if result:
                return result

        if file_path:
            result = self._resolve_via_import_map(name, file_path)
            if result:
                return result

        return self._resolve_via_global_symbols(name, file_path)

    def resolve_dep_candidate(
        self, name: str, source_fqn: str, edge_file_path: str | None
    ) -> str | None:
        """Resolve a raw_dep: candidate to a VARIABLE (DI alias) node, or None.

        Order: the consuming file's import map first, then the VARIABLE-only
        symbol index with same-file preference. Returns None for anything that
        is not an existing VARIABLE node — the caller drops the edge.
        A globally-unique match is accepted even when the name is not importable
        in the consuming file — matching resolve_global_call's behavior.

        When ``name`` is present in the file's import map, the import is
        authoritative: return the hit only if it is a VARIABLE node, otherwise
        return ``None`` immediately — never fall through to the global
        ``variable_symbols`` index.  This prevents an explicitly imported
        class from being shadowed by a same-named DI alias in another module.
        """
        file_path = self._index.normalized_file_path(source_fqn, edge_file_path)
        if file_path:
            via_import = self._resolve_via_import_map(name, file_path)
            if via_import:
                return via_import if self._index.is_variable_node(via_import) else None
        candidates = self._index.variable_symbols.get(name, [])
        if len(candidates) == 1:
            return candidates[0]
        if candidates and file_path:
            same_file = self._index.file_variable_symbols.get((file_path, name), [])
            if len(same_file) == 1:
                return same_file[0]
        return None

    def _resolve_method_on_class_hierarchy(
        self, class_fqn: str, method_name: str, visited: set[str]
    ) -> str | None:
        """DFS over EXTENDS edges to find method_name defined in class_fqn or any ancestor."""
        if class_fqn in visited:
            return None
        visited.add(class_fqn)
        direct = self._index.class_methods.get(class_fqn, {}).get(method_name)
        if direct:
            return direct
        for parent_fqn in self._inheritance_tree.get(class_fqn, []):
            result = self._resolve_method_on_class_hierarchy(parent_fqn, method_name, visited)
            if result:
                return result
        return None

    def _resolve_via_import_map(self, name: str, file_path: str) -> str | None:
        """Look up name in the file's import map (direct and module-prefixed calls)."""
        file_import_map = self._index.file_imports.get(file_path, {})

        if name in file_import_map:
            target_fqn = file_import_map[name]
            return self._index.map_to_node_fqn(target_fqn) or target_fqn

        first_part = name.split(".", maxsplit=1)[0]
        if first_part in file_import_map and "." in name:
            rest = name[len(first_part) + 1 :]
            target_fqn = f"{file_import_map[first_part]}.{rest}"
            return self._index.map_to_node_fqn(target_fqn) or target_fqn

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
        return self._index.map_to_node_fqn(candidate) or candidate

    def _resolve_via_global_symbols(self, name: str, file_path: str | None) -> str | None:
        """Look up name in the global symbol index, preferring same-file candidates."""
        candidates = self._index.global_symbols.get(name, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if file_path:
            same_file = self._index.file_global_symbols.get((file_path, name), [])
            if len(same_file) == 1:
                return same_file[0]
        return None
