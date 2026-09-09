"""Symbol resolution strategies over a SymbolIndex."""

from cgis.core.models import RAW_CLASS_PREFIX, Edge, EdgeType, Node, NodeNamespace
from cgis.resolver.indices import IndexBuilder, SymbolIndex


def _owning_class(index: SymbolIndex, source_fqn: str) -> str | None:
    """The nearest enclosing class of source_fqn that the index knows methods for.

    Walks up the FQN segments so a nested function (`mod.Cls.method.inner`)
    still finds `mod.Cls`.
    """
    parts = source_fqn.split(".")
    for i in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in index.class_methods:
            return candidate
    return None


def receiver_type_fqn(index: SymbolIndex, owner_fqn: str, attr: str) -> str | None:
    """Resolve `self.<attr>` on class `owner_fqn` to the type it was declared as.

    The declared type is a FQN the extractor built from the annotation and the
    file's import map, so it may carry a layout prefix the graph does not use
    (`cgis.x.Y` against a node id of `src.cgis.x.Y`); `map_to_node_fqn`
    reconciles those. The returned FQN is not guaranteed to have a node —
    a third-party type has none until the engine mints a virtual one — so the
    caller decides what an absent method means (see `resolve_self_call`).
    """
    declared = index.self_types.get(owner_fqn, {}).get(attr)
    if declared is None:
        return None
    return index.map_to_node_fqn(declared) or declared


def _declaring_class(
    index: SymbolIndex, owner_fqn: str, attr: str, inheritance: dict[str, list[str]]
) -> str | None:
    """The class in owner_fqn's hierarchy that declares `self.<attr>`, if any.

    A base class commonly holds the injected collaborator and the subclasses use
    it — `ReservationCore.__init__` taking the client, `ReservationCreation`
    calling `self.reservation_client.create()`. Looking only at the subclass's
    own self_types misses every such call. D7 already walks EXTENDS to find a
    *method* on the receiver; this walks the same tree to find the *attribute*
    on the owner.
    """
    seen: set[str] = set()
    stack = [owner_fqn]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if attr in index.self_types.get(current, {}):
            return current
        # Reversed because the stack is LIFO: extending in source order would pop
        # the *rightmost* base first, so `class C(Left, Right)` would answer with
        # Right's declaration where Python's MRO uses Left's. The sibling
        # _resolve_method_on_class_hierarchy recurses in source order and is
        # already correct; this keeps the two consistent.
        stack.extend(reversed(inheritance.get(current, [])))
    return None


class SymbolResolver:
    """Maps raw symbol names to graph FQNs.

    Strategy chain per call site: local variable types, the consuming file's
    import map, then the global symbol index with same-file preference.
    Holds the inheritance tree (a resolution product built from EXTENDS
    edges — not an index, see spec §2.4).

    The constructor accepts nodes and edges directly; it builds the
    SymbolIndex internally via IndexBuilder and exposes it as the public
    ``index`` attribute so callers (e.g. ResolverEngine) can read the index
    without building it themselves.
    """

    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        """Build the symbol index from nodes, then the inheritance tree from EXTENDS edges."""
        self.index: SymbolIndex = IndexBuilder().build(nodes)
        # class_fqn -> [resolved parent FQNs] built from EXTENDS edges
        self._inheritance_tree: dict[str, list[str]] = {}
        for edge in edges:
            if edge.type == EdgeType.EXTENDS:
                raw = edge.target.removeprefix(RAW_CLASS_PREFIX)
                resolved = self.resolve_class_ref(raw, edge.source, edge.file_path)
                # Only resolved (FQN) parents are useful for method-hierarchy lookup.
                # Storing the bare `raw` name would false-match an unrelated class
                # that happens to share that bare name (#183).
                if resolved:
                    self._inheritance_tree.setdefault(edge.source, []).append(resolved)

    def resolve_class_ref(
        self, name: str, source_fqn: str, edge_file_path: str | None
    ) -> str | None:
        """Resolve a class name from an EXTENDS edge target to a graph FQN."""
        file_path = self.index.normalized_file_path(source_fqn, edge_file_path)
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
        """Find the method a `self.…` call reaches, traversing inheritance.

        Two shapes. `self.helper()` arrives as "helper" and is looked up on the
        class that owns the source. `self.client.search()` arrives as
        "client.search": the receiver's declared type comes from `self_types`
        (spec D1), and the method is then searched on that class exactly as it
        would be on the owner — same walk, same inheritance rules (spec D7).

        Only one dot is handled. `self.a.b.c()` is left alone: resolving it
        would need the return type of `self.a.b`, which nothing records (D8).
        """
        owner = _owning_class(self.index, source_fqn)
        if owner is None:
            return None
        attr, sep, method = method_name.partition(".")
        if not sep:
            return self._resolve_method_on_class_hierarchy(owner, method_name, set())
        if "." in method:
            return None
        declaring = _declaring_class(self.index, owner, attr, self._inheritance_tree)
        if declaring is None:
            return None
        receiver = receiver_type_fqn(self.index, declaring, attr)
        if receiver is None:
            return None
        found = self._resolve_method_on_class_hierarchy(receiver, method, set())
        if found:
            return found
        # No such method on the declared receiver. Keep it only when the receiver
        # is genuinely a library type — a real call into a dependency, for which
        # the engine mints an EXTERNAL/STDLIB node. On an internal type it is a
        # phantom method and must not be fabricated: that is the call #414 was
        # filed to expose. Same policy as _resolve_local_type_call (spec D7).
        candidate = f"{receiver}.{method}"
        if self.index.classify_fqn(candidate) in (NodeNamespace.EXTERNAL, NodeNamespace.STDLIB):
            return candidate
        return None

    def resolve_global_call(
        self, name: str, source_fqn: str, edge_file_path: str | None = None
    ) -> str | None:
        """Resolve a global call using local types, import map, then global symbol index."""
        source_node = self.index.nodes.get(source_fqn)
        file_path = self.index.normalized_file_path(source_fqn, edge_file_path)

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
        file_path = self.index.normalized_file_path(source_fqn, edge_file_path)
        if file_path:
            via_import = self._resolve_via_import_map(name, file_path)
            if via_import:
                return via_import if self.index.is_variable_node(via_import) else None
        candidates = self.index.variable_symbols.get(name, [])
        if len(candidates) == 1:
            return candidates[0]
        if candidates and file_path:
            same_file = self.index.file_variable_symbols.get((file_path, name), [])
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
        direct = self.index.class_methods.get(class_fqn, {}).get(method_name)
        if direct:
            return direct
        for parent_fqn in self._inheritance_tree.get(class_fqn, []):
            result = self._resolve_method_on_class_hierarchy(parent_fqn, method_name, visited)
            if result:
                return result
        return None

    def _resolve_via_import_map(self, name: str, file_path: str) -> str | None:
        """Look up name in the file's import map (direct and module-prefixed calls)."""
        file_import_map = self.index.file_imports.get(file_path, {})

        if name in file_import_map:
            target_fqn = file_import_map[name]
            return self.index.map_to_node_fqn(target_fqn) or target_fqn

        first_part = name.split(".", maxsplit=1)[0]
        if first_part in file_import_map and "." in name:
            rest = name[len(first_part) + 1 :]
            target_fqn = f"{file_import_map[first_part]}.{rest}"
            return self.index.map_to_node_fqn(target_fqn) or target_fqn

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
        resolved = self.index.map_to_node_fqn(candidate)
        if resolved:
            return resolved
        # No node for `Class.method`. Keep it only when the type is external/stdlib
        # — that is a real call into a library (it becomes an EXTERNAL/STDLIB virtual
        # node). For an internal/unknown type it is a phantom method: drop it rather
        # than fabricate a node that does not exist (#183).
        if self.index.classify_fqn(candidate) in (NodeNamespace.EXTERNAL, NodeNamespace.STDLIB):
            return candidate
        return None

    def _resolve_via_global_symbols(self, name: str, file_path: str | None) -> str | None:
        """Look up name in the global symbol index, preferring same-file candidates."""
        candidates = self.index.global_symbols.get(name, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if file_path:
            same_file = self.index.file_global_symbols.get((file_path, name), [])
            if len(same_file) == 1:
                return same_file[0]
        return None
