"""Symbol resolution strategies over a SymbolIndex."""

import itertools
import os
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping

from cgis.core.models import RAW_CLASS_PREFIX, Edge, EdgeType, Node, NodeNamespace
from cgis.core.resolution import AFTER_IMPORT_MAP
from cgis.languages import dispatcher_for, rules_for, type_normalizers
from cgis.resolver.indices import IndexBuilder, SymbolIndex


def _is_local(name: str, source: Node, site: str | None) -> bool:
    """Whether the call's receiver name is a local of the source at the call's site."""
    sites: dict[str, str] = source.metadata.get("site_types") or {}
    if site in sites:
        return bool(sites[site])
    local_types: dict[str, str] = source.metadata.get("local_types") or {}
    return name.split(".", maxsplit=1)[0] in local_types


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


def _positioned(index: SymbolIndex, position: str, name: str, file_path: str) -> str | None:
    """The first hit among the source pack's steps at `position` (spec: Resolution)."""
    rules = rules_for(file_path)
    if rules is None:
        return None
    for step_position, step in rules.steps:
        if step_position == position:
            found = step(name, file_path, index)
            if found:
                return found
    return None


def _normalized(index: SymbolIndex, type_fqn: str) -> str:
    """`type_fqn` through every pack's type normalizers, in manifest order (step 4, D2)."""
    for normalize in type_normalizers():
        type_fqn = normalize(type_fqn, index)
    return type_fqn


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
    # Reconciled as the class's own file reads it: a Go field of an imported type
    # names an import path only the Go pack maps (#608). Python reads as before.
    source_file = _file_of(index, owner_fqn)
    # `self.svc = svc` with `svc: ServiceDep` declares the alias, not the service
    # behind it — the same gap as a parameter's own calls (#550 review).
    return _normalized(index, index.map_to_node_fqn(declared, source_file) or declared)


def _own_type(index: SymbolIndex, type_fqn: str, source_file: str | None) -> bool:
    """Whether `type_fqn` is a node declared in `source_file`'s own scope (its Go package,
    else the file itself).

    Any node of the graph is not enough: a Go id is a directory, so an own `context/`
    declaring `Context` has the stdlib type's id, and `ctx.Done()` stays the stdlib's.
    """
    node_file = _file_of(index, type_fqn)
    if node_file is None or source_file is None:
        return False
    source_file = os.path.normpath(source_file)
    scopes = index.file_scopes
    return scopes.get(node_file, node_file) == scopes.get(source_file, source_file)


def _origin_of(index: SymbolIndex, type_fqn: str, source_file: str | None) -> str | None:
    """The normalized file the caller's pack says `type_fqn` expands through, or None."""
    rules = rules_for(source_file)
    origin = rules.type_origin(type_fqn, index) if rules and rules.type_origin else None
    return os.path.normpath(origin) if origin else None


def _file_of(index: SymbolIndex, fqn: str) -> str | None:
    """The normalized file a node was extracted from, or None for no such node."""
    node = index.nodes.get(fqn)
    return os.path.normpath(node.file_path) if node is not None else None


#: A hit on a field, not a method: it stops the search and resolves nothing.
_SHADOWED = "\x00field"


def _declaring_class(
    index: SymbolIndex, owner_fqn: str, attr: str, inheritance: Mapping[str, list[str]]
) -> str | None:
    """The class in owner_fqn's hierarchy that declares `self.<attr>`, if any.

    A base class commonly holds the injected collaborator and the subclasses use
    it — `ReservationCore.__init__` taking the client, `ReservationCreation`
    calling `self.reservation_client.create()`. Looking only at the subclass's
    own self_types misses every such call. D7 already walks EXTENDS to find a
    *method* on the receiver; this walks the same tree to find the *attribute*
    on the owner.
    """

    def found(fqn: str) -> str | None:
        if attr in index.self_types.get(fqn, {}):
            return fqn
        node = index.nodes.get(fqn)
        # A Go field of no named type (`X struct{...}`) still hides a deeper `X`.
        return _SHADOWED if node is not None and attr in node.metadata.get("fields", ()) else None

    hit = _find_above(index, inheritance, owner_fqn, found)
    return None if hit == _SHADOWED else hit


def _method_in_hierarchy(
    index: SymbolIndex, inheritance: Mapping[str, list[str]], class_fqn: str, method_name: str
) -> str | None:
    """The method `method_name` on class_fqn or the nearest type above it that has one.

    A Go field of that name at a depth shadows any method below it (`fields`); a
    call through the field is to a function value, so nothing resolves.
    """

    def found(fqn: str) -> str | None:
        method = index.class_methods.get(fqn, {}).get(method_name)
        node = index.nodes.get(fqn)
        if method is None and node is not None and method_name in node.metadata.get("fields", ()):
            return _SHADOWED
        return method

    hit = _find_above(index, inheritance, class_fqn, found)
    return None if hit == _SHADOWED else hit


def _find_above(
    index: SymbolIndex,
    inheritance: Mapping[str, list[str]],
    start: str,
    found: Callable[[str], str | None],
) -> str | None:
    """The first hit of `found` on `start` or a type above it, in the start's language's order."""
    node = index.nodes.get(start)
    rules = rules_for(node.file_path) if node is not None else None
    if rules is not None and rules.shallowest_member:
        return _shallowest(inheritance, start, found)
    mro = _c3(start, inheritance)
    if mro is not None:
        return next((hit for hit in map(found, mro) if hit), None)
    # No consistent linearisation (a cycle, or bases Python would reject): the
    # depth-first order Python's MRO matches outside a diamond.
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        hit = found(current)
        if hit:
            return hit
        # Reversed because the stack is LIFO: extending in source order would pop
        # the *rightmost* base first, so `class C(Left, Right)` would answer with
        # Right's member where Python's MRO uses Left's.
        stack.extend(reversed(inheritance.get(current, [])))
    return None


def _c3(start: str, inheritance: Mapping[str, list[str]]) -> list[str] | None:
    """Python's method resolution order for `start` (C3), or None when it has none (#658).

    Iterative, so a deep hierarchy cannot exhaust the stack: each class is
    linearised once its bases are.
    """
    memo: dict[str, list[str]] = {}
    on_path: set[str] = set()
    stack = [(start, False)]
    while stack:
        current, expanded = stack.pop()
        if current in memo:
            continue
        bases = inheritance.get(current, [])
        if not expanded:
            if current in on_path:
                return None  # an inheritance cycle
            on_path.add(current)
            stack.append((current, True))
            stack.extend((b, False) for b in reversed(bases) if b not in memo)
            continue
        on_path.discard(current)
        merged = _merge([list(memo[b]) for b in bases] + [list(bases)])
        if merged is None:
            return None
        memo[current] = [current, *merged]
    return memo[start]


def _merge(sequences: list[list[str]]) -> list[str] | None:
    """C3's merge: the next head no other sequence holds in its tail; None when stuck.

    A count of tail occurrences answers "in any tail" in O(1), so a long chain
    costs its length, not its length cubed (#656 review).
    """
    queues = [deque(s) for s in sequences if s]
    in_tails = Counter(name for q in queues for name in itertools.islice(q, 1, None))
    merged: list[str] = []
    while queues:
        head = next((q[0] for q in queues if not in_tails[q[0]]), None)
        if head is None:
            return None
        merged.append(head)
        for q in queues:
            if q[0] == head:
                q.popleft()
                if q:
                    in_tails[q[0]] -= 1
        queues = [q for q in queues if q]
    return merged


def _shallowest(
    inheritance: Mapping[str, list[str]], start: str, found: Callable[[str], str | None]
) -> str | None:
    """Go's selector rule: the one hit at the shallowest depth; two there are ambiguous."""
    level, seen = [start], {start}
    while level:
        hits = {hit for hit in map(found, level) if hit}
        if hits:
            return hits.pop() if len(hits) == 1 else None
        level = [p for c in level for p in inheritance.get(c, []) if p not in seen]
        seen.update(level)
    return None


def _resolve_on_type(
    index: SymbolIndex,
    inheritance: Mapping[str, list[str]],
    type_fqn: str,
    path: str,
    source_file: str | None,
    claims: dict[str, NodeNamespace] | None = None,
) -> str | None:
    """`a.b.m` on a value of `type_fqn`: each attribute's declared type, then `m` (#643).

    Attributes and the method are both looked up through the hierarchy: a base
    class, or a Go struct an embedding promotes from, may hold either. A library
    method kept at the end is recorded in `claims` with the namespace it was kept
    for, read from the file that declared the last field (#685).
    """
    *attrs, method = path.split(".")
    current = type_fqn
    for attr in attrs:
        declaring = _declaring_class(index, current, attr, inheritance)
        receiver = receiver_type_fqn(index, declaring, attr) if declaring else None
        if declaring is None or receiver is None:
            return None
        current = receiver
        # The field's type was named in its declaring file, so that file's imports
        # say what it is, not the caller's (#685).
        source_file = _file_of(index, declaring) or source_file
    found = _method_in_hierarchy(index, inheritance, current, method)
    if found:
        return found
    # No such method on the declared receiver. Keep it only when the receiver
    # is genuinely a library type — a real call into a dependency, for which
    # the engine mints an EXTERNAL/STDLIB node. On an internal type it is a
    # phantom method and must not be fabricated: that is the call #414 was
    # filed to expose. Same policy as _resolve_local_type_call (spec D7).
    if _own_type(index, current, source_file):
        # The receiver is declared in the classifying file's own package: the method
        # it lacks is a phantom, even where that package shares a name with an import
        # it makes (`errors` importing the stdlib's, #691 review).
        return None
    candidate = f"{current}.{method}"
    namespace = index.classify_fqn(candidate, source_file)
    if namespace in (NodeNamespace.EXTERNAL, NodeNamespace.STDLIB):
        if claims is not None:
            claims.setdefault(candidate, namespace)
        return candidate
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

    def __init__(
        self,
        nodes: list[Node],
        edges: list[Edge],
        workspace_packages: Mapping[str, str] | None = None,
        tsconfig_paths: Mapping[str, Mapping[str, list[str]]] | None = None,
        dependency_roots: Iterable[str] = (),
        npm_dependencies: Iterable[str] = (),
        project_state: Mapping[str, object] | None = None,
    ) -> None:
        """Build the symbol index from nodes, then the inheritance tree from EXTENDS edges."""
        self.index: SymbolIndex = IndexBuilder().build(
            nodes,
            workspace_packages,
            tsconfig_paths,
            dependency_roots,
            npm_dependencies,
            project_state,
        )
        # class_fqn -> [resolved parent FQNs] built from EXTENDS edges; read by the
        # DISPATCHES pass too (`dispatch_edges`, below).
        self.inheritance_tree: dict[str, list[str]] = {}
        # Library targets resolved through a field declared in another file, with the
        # namespace that file's imports gave them: the caller's file may not import the
        # package, so the engine must not classify them again from there (#685).
        self.boundary_claims: dict[str, NodeNamespace] = {}
        for edge in edges:
            if edge.type == EdgeType.EXTENDS:
                raw = edge.target.removeprefix(RAW_CLASS_PREFIX)
                resolved = self.resolve_class_ref(raw, edge.source, edge.file_path)
                # Only resolved (FQN) parents are useful for method-hierarchy lookup.
                # Storing the bare `raw` name would false-match an unrelated class
                # that happens to share that bare name (#183).
                if resolved:
                    self.inheritance_tree.setdefault(edge.source, []).append(resolved)

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
        source = self.index.nodes.get(source_fqn)
        language = source.language if source else None
        resolved = self._resolve_via_global_symbols(query_name, file_path, language)
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
        `self.a.b.c()` takes each hop's declared type in turn (#643; D8 held
        that nothing recorded them, which `self_types` on every class now does).
        """
        owner = _owning_class(self.index, source_fqn)
        if owner is None:
            return None
        if "." not in method_name:
            return _method_in_hierarchy(self.index, self.inheritance_tree, owner, method_name)
        source_file = _file_of(self.index, source_fqn)
        return _resolve_on_type(
            self.index, self.inheritance_tree, owner, method_name, source_file, self.boundary_claims
        )

    def resolve_global_call(
        self,
        name: str,
        source_fqn: str,
        edge_file_path: str | None = None,
        site: str | None = None,
    ) -> str | None:
        """Resolve a global call using local types, import map, then global symbol index.

        `site` is the call's raw name and position, as its edge id ends; a source's `site_types` may
        say what the receiver name is there, over its `local_types` (#643 B8).
        """
        source_node = self.index.nodes.get(source_fqn)
        file_path = self.index.normalized_file_path(source_fqn, edge_file_path)

        if source_node:
            result = self._resolve_local_type_call(name, source_node, file_path, site)
            if result:
                return result
            rules = rules_for(file_path)
            if rules is not None and rules.locals_shadow and _is_local(name, source_node, site):
                return None

        if file_path:
            result = self._resolve_via_import_map(name, file_path) or _positioned(
                self.index, AFTER_IMPORT_MAP, name, file_path
            )
            if result:
                return result

        language = source_node.language if source_node else None
        return self._resolve_via_global_symbols(name, file_path, language)

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
            same_file = self.index.file_variable_symbols.get(
                (self.index.file_scopes.get(file_path, file_path), name), []
            )
            if len(same_file) == 1:
                return same_file[0]
        return None

    def _resolve_via_import_map(self, name: str, file_path: str) -> str | None:
        """Look up name in the file's import map (direct and module-prefixed calls)."""
        file_import_map = self.index.file_imports.get(file_path, {})

        if name in file_import_map:
            target_fqn = file_import_map[name]
            return self.index.map_to_node_fqn(target_fqn, file_path) or target_fqn

        first_part = name.split(".", maxsplit=1)[0]
        if first_part in file_import_map and "." in name:
            rest = name[len(first_part) + 1 :]
            target_fqn = f"{file_import_map[first_part]}.{rest}"
            return self.index.map_to_node_fqn(target_fqn, file_path) or target_fqn

        return None

    def _resolve_local_type_call(
        self, name: str, source_node: Node, file_path: str | None, site: str | None = None
    ) -> str | None:
        """Resolve `var.method` using local_types metadata on the source node.

        `file_path` is the source's: the type is classified as its language reads it.
        A `site_types` entry for the call's position wins over `local_types`: the
        name's binding in that block, or "" where it is not a local at all.
        """
        if "." not in name:
            return None
        var_name, method_name = name.split(".", maxsplit=1)
        local_types: dict[str, str] = source_node.metadata.get("local_types") or {}
        sites: dict[str, str] = source_node.metadata.get("site_types") or {}
        class_fqn = sites[site] if site in sites else local_types.get(var_name)
        if not class_fqn:
            return None
        # An expanded type was named in the file that declared it, so that file's
        # imports classify it, not the caller's (#688).
        classify_in = _origin_of(self.index, class_fqn, file_path) or file_path
        class_fqn = _normalized(self.index, class_fqn)
        candidate = f"{class_fqn}.{method_name}"
        resolved = self.index.map_to_node_fqn(candidate, file_path)
        if resolved:
            return resolved
        # Inherited or promoted, or reached through declared attributes (#643).
        start = self.index.map_to_node_fqn(class_fqn, file_path) or class_fqn
        walked = _resolve_on_type(
            self.index, self.inheritance_tree, start, method_name, classify_in, self.boundary_claims
        )
        if walked:
            return walked
        # Reached for `x.a.m` on a library type, where the walk has no declared
        # attribute to follow, and for a library type a suffix match mapped onto an
        # own one, where the walk stopped at the own type.
        # No node for `Class.method`. Keep it only when the type is external/stdlib
        # — that is a real call into a library (it becomes an EXTERNAL/STDLIB virtual
        # node). For an internal/unknown type it is a phantom method: drop it rather
        # than fabricate a node that does not exist (#183).
        if _own_type(self.index, class_fqn, classify_in):
            # A phantom, as in `_resolve_on_type`. The unmapped type, not `start`: a
            # suffix match may map a library type onto an own one (`regexp.Regexp` →
            # `cdc.regexp.Regexp`), and the call is still the library's.
            return None
        namespace = self.index.classify_fqn(candidate, classify_in)
        if namespace in (NodeNamespace.EXTERNAL, NodeNamespace.STDLIB):
            self.boundary_claims.setdefault(candidate, namespace)
            return candidate
        return None

    def _resolve_via_global_symbols(
        self, name: str, file_path: str | None, language: str | None = None
    ) -> str | None:
        """Look up name in the global symbol index, preferring same-file candidates.

        With `language`, only that language's symbols are candidates: a bare name in one
        language never binds to another's (#675).
        """
        candidates = self.index.global_symbols.get(name, [])
        if language is not None:
            candidates = [
                c for c in candidates if (n := self.index.nodes.get(c)) and n.language == language
            ]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if file_path:
            same_file = self.index.file_global_symbols.get(
                (self.index.file_scopes.get(file_path, file_path), name), []
            )
            if len(same_file) == 1:
                return same_file[0]
        return None


# --- DISPATCHES (#545) -------------------------------------------------------
#
# A call resolves to the method its receiver's declared type has, so a call through
# a base lands on the base method, and nothing recorded that a subclass override may
# run instead: impact on the override missed every caller going through the base.
# One edge links the nearest ancestor that defines a method to each class that
# redefines it — class hierarchy analysis, with no attempt to tell which receivers
# can actually hold the subclass, hence a type of its own at a lower confidence.
# Dunder methods and overrides in test code are left out.
#
# Pure over methods and the inheritance tree: the resolver runs it on what it
# extracted, and the pipeline re-runs it over the whole stored graph after every
# write — an incremental run sees only changed files' EXTENDS (`refresh_dispatch`).
# Module-level, beside `_package_call`: `SymbolResolver` is at the god-object
# threshold, and a module of its own moved `cgis.resolver`'s self-drift past
# `pipeline_stage`'s tolerance.

#: Below a resolved call (1.0) and an import-map hit (0.8): the receiver may never
#: hold the subclass at all.
DISPATCH_CONFIDENCE = 0.5


def dispatch_edges(
    methods: Iterable[Node], inheritance: Mapping[str, list[str]], classes: Iterable[Node] = ()
) -> list[Edge]:
    """Every DISPATCHES edge, each language's types under its own pack's rule (#609).

    A pack whose rules name no `dispatch` gets class hierarchy analysis
    (`_hierarchy_dispatch`). Types of different languages never dispatch to each
    other, so methods and classes are split by `Node.language` first.
    """
    methods_by: dict[str, list[Node]] = {}
    for method in methods:
        methods_by.setdefault(method.language, []).append(method)
    classes_by: dict[str, list[Node]] = {}
    for cls in classes:
        classes_by.setdefault(cls.language, []).append(cls)
    edges: list[Edge] = []
    for language, own in methods_by.items():
        rule = dispatcher_for(language)
        if rule is None:
            edges.extend(_hierarchy_dispatch(own, inheritance))
        else:
            edges.extend(rule(own, classes_by.get(language, []), inheritance))
    return edges


def _hierarchy_dispatch(
    methods: Iterable[Node], inheritance: Mapping[str, list[str]]
) -> list[Edge]:
    """One DISPATCHES edge from each overridden method to its override.

    The source is the nearest definition up each parent's line, so a chain of
    overrides becomes a chain of edges and a method a middle class does not
    redefine still links the grandparent to the grandchild. With several
    parents (or a diamond) an override may get more than one edge, never the
    same one twice.
    """
    by_class: dict[str, dict[str, Node]] = {}
    for method in methods:
        cls, _, name = method.id.rpartition(".")
        by_class.setdefault(cls, {})[name] = method
    edges: dict[str, Edge] = {}
    for cls, parents in inheritance.items():
        for name, override in by_class.get(cls, {}).items():
            if override.is_test or _is_dunder(name):
                continue
            for parent in parents:
                base = _defined_in_ancestry(by_class, inheritance, parent, name)
                if base is None:
                    continue
                edge_id = f"dispatch:{base}->{override.id}"
                edges[edge_id] = Edge(
                    id=edge_id,
                    source=base,
                    target=override.id,
                    type=EdgeType.DISPATCHES,
                    confidence=DISPATCH_CONFIDENCE,
                    context=f"override of {base}",
                    file_path=override.file_path,
                    line_number=override.start_line,
                )
    return list(edges.values())


def _defined_in_ancestry(
    by_class: Mapping[str, Mapping[str, Node]],
    inheritance: Mapping[str, list[str]],
    cls: str,
    name: str,
) -> str | None:
    """The method `name` as `cls` or its nearest ancestor defines it, depth first."""
    seen: set[str] = set()
    stack = [cls]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        method = by_class.get(current, {}).get(name)
        if method is not None:
            return method.id
        stack.extend(reversed(inheritance.get(current, [])))
    return None


def _is_dunder(name: str) -> bool:
    """`__init__`, `__eq__` and the like."""
    return name.startswith("__") and name.endswith("__")
