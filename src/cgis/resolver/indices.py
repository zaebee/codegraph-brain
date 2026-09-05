"""Symbol indices for FQN resolution: built once by IndexBuilder, read-only after."""

import builtins
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from cgis.core.models import SELF_PREFIX, Node, NodeNamespace, NodeType

_BUILTINS: frozenset[str] = frozenset(dir(builtins))


@dataclass(frozen=True)
class SymbolIndex:
    """Immutable lookup indices over the extracted node set.

    Built by IndexBuilder, consumed by SymbolResolver and ResolverEngine.

    Immutable in substance, not only by convention: the dataclass prevents
    field rebinding, and ``IndexBuilder`` hands over read-only views, so a
    stray write (a cache line added to a shared index, say) raises instead of
    silently corrupting resolution for every later lookup (#183).

    The views are shallow — the inner ``list``/``dict`` values are still
    mutable. Deep-freezing would mean copying every one of them on a hot path;
    the realistic mistake this guards is adding or replacing a top-level key.
    """

    # node id (FQN) -> Node
    nodes: Mapping[str, Node]
    # name -> list of FQNs
    global_symbols: Mapping[str, list[str]]
    # (file_path, name) -> list of FQNs (list handles conditional redefinitions)
    file_global_symbols: Mapping[tuple[str, str], list[str]]
    # class_fqn -> {method_name -> method_fqn}
    class_methods: Mapping[str, dict[str, str]]
    # class_fqn -> {attribute_name -> declared type FQN}, from CLASS node metadata.
    # Written by the extractor (spec D1), read by resolve_self_call (spec D7).
    self_types: Mapping[str, dict[str, str]]
    # DI-alias (VARIABLE) indices for raw_dep: resolution; kept separate
    # from global_symbols so call resolution behavior does not change.
    variable_symbols: Mapping[str, list[str]]
    file_variable_symbols: Mapping[tuple[str, str], list[str]]
    # normalized file_path -> {local_alias: target_fqn}  (from FILE node import_map)
    file_imports: Mapping[str, dict[str, str]]
    # module_fqn -> {name: definer_fqn}: names a module forwards rather than defines,
    # from explicit re-exports and expanded star imports (#417).
    reexports: Mapping[str, dict[str, str]]
    # suffix_fqn -> [full_node_ids]  (handles src/ layout prefix mismatch)
    suffix_map: Mapping[str, list[str]]
    # top-level root segments of all internal nodes (for classify)
    internal_roots: frozenset[str]
    # root segments of absolute imports (anything else is UNKNOWN)
    external_roots: frozenset[str]

    def map_to_node_fqn(self, imported_fqn: str) -> str | None:
        """Resolve an imported FQN to an actual node in the graph.

        Handles two common prefix-mismatch patterns:
        - Import has extra package prefix: `cgis.resolver.engine.X` → node `resolver.engine.X`
          (strip leading segments from the imported FQN)
        - Node has extra layout prefix: `cgis.pipeline.X` → node `src.cgis.pipeline.X`
          (look up in suffix_map built from node IDs)

        Returns None when the target is ambiguous or not in the graph.
        """
        direct = self.resolve_layout(imported_fqn)
        if direct is not None:
            return direct
        parts = imported_fqn.split(".")
        # A package that forwards the name rather than defining it: `from app.models
        # import Rating` where app/models/__init__.py re-exports it from .rating.
        # Without this the consumer's FQN has no node and the edge dies (#417).
        #
        # Tried on the stripped forms too, not just the whole FQN: the import says
        # `app.models.Rating` while the re-export map is keyed by node id, which is
        # `models` when the project was ingested at app/. Skipping this is why the
        # first version of the fix resolved nothing on the codebase it was written
        # for.
        for i in range(len(parts) - 1):
            forwarded = self.follow_reexport(".".join(parts[i:]))
            if forwarded is not None:
                resolved = self.resolve_layout(forwarded)
                if resolved is not None:
                    return resolved
        return None

    def resolve_layout(self, fqn: str) -> str | None:
        """Match an FQN to a node id, reconciling layout prefixes. No re-exports.

        Three shapes, most precise first: the id verbatim; a node whose id has an
        extra layout prefix (`cgis.pipeline.X` against `src.cgis.pipeline.X`);
        and an FQN carrying a prefix the ids do not (`app.crud.X` against
        `crud.X`, when the project was ingested at app/).

        Separate from `map_to_node_fqn` because the re-export walk needs exactly
        this and must not recurse back into the re-export step.
        """
        if fqn in self.nodes:
            return fqn
        candidates = self.suffix_map.get(fqn, [])
        if len(candidates) == 1:
            return candidates[0]
        parts = fqn.split(".")
        for i in range(1, len(parts)):
            candidate = ".".join(parts[i:])
            if candidate in self.nodes:
                return candidate
        return None

    def follow_reexport(self, fqn: str) -> str | None:
        """Resolve `module.name` through every forwarding hop, or None if not forwarded.

        One hop is not enough. `app.Rating` -> `app.models.Rating` ->
        `app.models.rating.Rating` is the ordinary shape of a package that
        re-exports from a subpackage that re-exports from a module, and stopping
        at the middle leaves the consumer pointing at something with no node.
        The visited set makes a re-export cycle terminate rather than hang.
        """
        via, _, name = fqn.rpartition(".")
        forwarded = self.reexports.get(via, {}).get(name)
        if forwarded is None:
            return None
        seen = {fqn}
        while forwarded not in seen:
            seen.add(forwarded)
            # A hop that is itself a node is the answer: a package that both stars
            # a submodule and defines the name would otherwise be walked past, and
            # the consumer would land on the submodule's copy.
            if self.resolve_layout(forwarded) is not None:
                break
            next_via, _, next_name = forwarded.rpartition(".")
            next_hop = self.reexports.get(next_via, {}).get(next_name)
            if next_hop is None:
                break
            forwarded = next_hop
        return forwarded

    def has_node(self, fqn: str) -> bool:
        """True if the graph holds a node with this exact FQN.

        Saves callers reaching into ``nodes`` for a membership test, which is
        the one place the index's internals leaked (#183).
        """
        return fqn in self.nodes

    def classify_fqn(self, fqn: str) -> NodeNamespace:
        """Classify an FQN as STDLIB, INTERNAL, EXTERNAL, or UNKNOWN.

        UNKNOWN means the root segment was not found in internal roots,
        stdlib/builtins, or any known import-map external root.
        """
        if fqn.startswith("."):
            return NodeNamespace.INTERNAL
        if fqn.startswith(SELF_PREFIX):
            # An unresolved receiver, not a symbol. UNKNOWN rather than INTERNAL
            # so get_edge_stats counts it as unresolved: classifying it INTERNAL
            # let unresolved_ratio *improve* as a codebase adopted more dependency
            # injection, so a green `validate` said nothing about the gap (#414).
            return NodeNamespace.UNKNOWN
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


def _strips_to_a_node(imported_fqn: str, node_ids: set[str]) -> bool:
    """True when dropping leading segments of `imported_fqn` reaches a real node.

    `app.domains.models.Thing` against a graph rooted at `domains` strips to
    `domains.models.Thing`, which exists — so `app` is this project's own name,
    not a package it depends on.

    The remainder must keep at least two segments. Stripping to a single one
    matches any library whose submodule shares a name with a top-level module of
    ours — `from pydantic import config` against a project that has `config.py`
    strips to `config`, and the whole of pydantic would then classify INTERNAL,
    breaking resolution for every symbol in it. Two segments cannot collide by
    accident, and a genuine first-party prefix always has deeper imports than
    that: on owner-api 3346 values resolve, none of them needing this.
    """
    parts = imported_fqn.split(".")
    return any(".".join(parts[i:]) in node_ids for i in range(1, len(parts) - 1))


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
        self_types: dict[str, dict[str, str]] = {}
        variable_symbols: dict[str, list[str]] = {}
        file_variable_symbols: dict[tuple[str, str], list[str]] = {}
        file_imports: dict[str, dict[str, str]] = {}
        reexports: dict[str, dict[str, str]] = {}
        star_imports: dict[str, list[str]] = {}
        suffix_map: dict[str, list[str]] = {}
        internal_roots: set[str] = set()

        for node in nodes_by_id.values():
            # Maps the extractor attached to FILE and CLASS nodes.
            self._index_metadata(node, file_imports, self_types, reexports, star_imports)

            # Index global functions/symbols
            if node.type in (NodeType.FUNCTION, NodeType.CLASS):
                self._index_by_name(node, global_symbols, file_global_symbols)

            # Index methods within classes
            if node.type == NodeType.METHOD:
                class_fqn, sep, _ = node.id.rpartition(".")
                if sep:
                    class_methods.setdefault(class_fqn, {})[node.name] = node.id

            # Index DI aliases for raw_dep: candidate resolution
            if node.type == NodeType.VARIABLE:
                self._index_by_name(node, variable_symbols, file_variable_symbols)

            # Build suffix map for src/-layout prefix normalization:
            # "src.cgis.pipeline.X" → suffix "cgis.pipeline.X" also points to the node
            self._add_node_to_suffix_map(node.id, suffix_map, internal_roots)

        external_roots, first_party = self._build_external_roots(file_imports, set(nodes_by_id))
        self._expand_star_imports(star_imports, reexports, set(nodes_by_id))

        # Read-only views, not copies: MappingProxyType wraps in O(1), so this
        # buys write protection without touching ingest cost (#183).
        return SymbolIndex(
            nodes=MappingProxyType(nodes_by_id),
            global_symbols=MappingProxyType(global_symbols),
            file_global_symbols=MappingProxyType(file_global_symbols),
            class_methods=MappingProxyType(class_methods),
            self_types=MappingProxyType(self_types),
            variable_symbols=MappingProxyType(variable_symbols),
            file_variable_symbols=MappingProxyType(file_variable_symbols),
            file_imports=MappingProxyType(file_imports),
            reexports=MappingProxyType(reexports),
            suffix_map=MappingProxyType(suffix_map),
            internal_roots=frozenset(internal_roots | first_party),
            external_roots=frozenset(external_roots),
        )

    @staticmethod
    def _public_names_under(module_fqn: str, node_ids: set[str]) -> dict[str, str]:
        """The symbols `from <module_fqn> import *` would bring in directly.

        Only names defined immediately under the module: a dotted remainder is a
        symbol inside one of its children, and a leading underscore is private —
        `import *` takes neither.

        The module is tried verbatim and then with leading segments stripped. A
        star import written absolutely (`from app.tests.fixtures import *`)
        records the path as the author typed it, while node ids are
        `tests.fixtures` when the project was ingested at app/ — the same prefix
        mismatch that made the first version of this feature resolve nothing, and
        it reaches the star sources as well as the map keys.
        """
        parts = module_fqn.split(".")
        for i in range(len(parts)):
            prefix = ".".join(parts[i:]) + "."
            names: dict[str, str] = {}
            for node_id in node_ids:
                if not node_id.startswith(prefix):
                    continue
                name = node_id[len(prefix) :]
                if "." not in name and not name.startswith("_"):
                    names.setdefault(name, node_id)
            if names:
                return names
        return {}

    @staticmethod
    def _close_over_sources(
        by_module: dict[str, dict[str, str]],
        star_imports: dict[str, list[str]],
        reexports: dict[str, dict[str, str]],
    ) -> None:
        """Pull each source's own forwarded names in, repeatedly, until stable.

        A star import of a package that itself re-exports needs those names too:
        `app/__init__` starring `app.models`, which stars `app.models.rating`,
        must reach Rating. Repeating terminates because names are only ever
        added, never removed.
        """
        changed = True
        while changed:
            changed = False
            for module_fqn, sources in star_imports.items():
                target = by_module[module_fqn]
                for source in sources:
                    inherited = {**reexports.get(source, {}), **by_module.get(source, {})}
                    for name, definer in inherited.items():
                        if name not in target:
                            target[name] = definer
                            changed = True

    @classmethod
    def _expand_star_imports(
        cls,
        star_imports: dict[str, list[str]],
        reexports: dict[str, dict[str, str]],
        node_ids: set[str],
    ) -> None:
        """Turn `from X import *` into the names X actually exports.

        The extractor cannot do this: it sees one file, and the names belong to
        the *exporting* module. Here every node is known.

        `__all__` is not read. Where a module has one, the difference is a module
        deliberately hiding a public name — rare, and the extra entry is inert:
        it only ever fires if some file actually imports that name.

        An explicit re-export already recorded wins over a starred one: a module
        that both stars a submodule and names a symbol explicitly meant the
        explicit one.
        """
        by_module = {
            module_fqn: {
                name: definer
                for source in sources
                for name, definer in cls._public_names_under(source, node_ids).items()
            }
            for module_fqn, sources in star_imports.items()
        }
        cls._close_over_sources(by_module, star_imports, reexports)
        for module_fqn, names in by_module.items():
            if not names:
                continue
            existing = reexports.setdefault(module_fqn, {})
            for name, definer in names.items():
                existing.setdefault(name, definer)

    @staticmethod
    def _index_metadata(
        node: Node,
        file_imports: dict[str, dict[str, str]],
        self_types: dict[str, dict[str, str]],
        reexports: dict[str, dict[str, str]],
        star_imports: dict[str, list[str]],
    ) -> None:
        """Read the maps the extractor attached to a FILE or CLASS node.

        A FILE carries its import map; a CLASS carries the attribute types
        recorded from annotations (spec D1). Both are extractor output the
        resolver consumes rather than anything derived here.
        """
        if node.type == NodeType.FILE:
            file_imports[os.path.normpath(node.file_path)] = node.metadata.get("import_map") or {}
            forwarded = node.metadata.get("reexports")
            if forwarded:
                reexports[node.id] = dict(forwarded)
            wildcards = node.metadata.get("star_imports")
            if wildcards:
                star_imports[node.id] = list(wildcards)
        elif node.type == NodeType.CLASS:
            declared = node.metadata.get("self_types")
            if declared:
                self_types[node.id] = declared

    @staticmethod
    def _index_by_name(
        node: Node,
        by_name: dict[str, list[str]],
        by_file_and_name: dict[tuple[str, str], list[str]],
    ) -> None:
        """Record a node under its bare name, and under (file, name).

        The global-symbol and DI-alias indices are the same shape over
        different node types, and were the same four lines written twice.
        """
        by_name.setdefault(node.name, []).append(node.id)
        by_file_and_name.setdefault((os.path.normpath(node.file_path), node.name), []).append(
            node.id
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

    def _build_external_roots(
        self, file_imports: dict[str, dict[str, str]], node_ids: set[str]
    ) -> tuple[set[str], set[str]]:
        """Split import roots into genuinely external ones and first-party prefixes.

        Returns (external_roots, first_party_prefixes).

        A root is a *first-party prefix* when some import under it reaches a real
        node once its head is stripped. That happens whenever a project is
        ingested at a subdirectory: `cgis ingest ownima-backend/app` roots its
        nodes at `domains`, `api`, … while the code imports `from app.models
        import X`, so `app` looks like a third-party package. Every `app.*` FQN
        without a node then classified EXTERNAL, and the resolver minted a
        boundary node for a class of ours that does not exist — 2 461 confident
        edges to nothing on that codebase (#424).

        The discriminator is unambiguous in practice: measured there, `app` had
        3 346 of 4 885 import values reach a node while pydantic, sqlalchemy,
        grpc, fastapi and httpx had zero each. It asks whether the *import value*
        resolves, not whether some node's id ends with the root's name — the
        latter would match the library `grpc` against a project's own
        `api/dependencies/grpc/` package.
        """
        imports_by_root: dict[str, set[str]] = {}
        for import_map in file_imports.values():
            for val in import_map.values():
                if val and not val.startswith("."):
                    imports_by_root.setdefault(val.split(".", maxsplit=1)[0], set()).add(val)

        external: set[str] = set()
        first_party: set[str] = set()
        for root, values in imports_by_root.items():
            resolves = any(_strips_to_a_node(v, node_ids) for v in values)
            (first_party if resolves else external).add(root)
        return external, first_party
