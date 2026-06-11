# Resolver Split (IndexBuilder + SymbolResolver) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the `ResolverEngine` God Object (23 methods) into `SymbolIndex` + `IndexBuilder` (indices.py), `SymbolResolver` (symbols.py), and a 7-method `ResolverEngine` facade — behavior preserved bit-for-bit.

**Architecture:** Per `docs/specs/2026-06-11-resolver-split-design.md`. `IndexBuilder` turns nodes (no edges) into a frozen `SymbolIndex` carrying 10 lookup dicts + 4 lookup methods. `SymbolResolver` holds the 9 resolution-strategy methods and builds the inheritance tree in `__init__` (it is a resolution product, not an index). `ResolverEngine` keeps edge finalization (confidence policy, virtual nodes) and the public `resolve()` API.

**Tech Stack:** Python 3.12, Pydantic frozen models, dataclasses, pytest, mypy strict, ruff, interrogate ≥90%.

**Branch:** `feat/issue-115-resolver-split`

**Hard rules for every task:**
- Method bodies move **verbatim** — only mechanical substitutions listed per task. Never "improve" logic, confidence values, or resolution order.
- After implementing, run the **FULL unit suite** (`uv run pytest tests/unit/ -q`), not just the task's test file (lesson from #161: a cross-suite failure hid behind a green task-local run).
- All new modules/classes/methods need docstrings (interrogate ≥90% is a CI gate).

---

### Task 1: `indices.py` — SymbolIndex + IndexBuilder (TDD)

**Files:**
- Create: `src/cgis/resolver/indices.py`
- Create: `tests/unit/test_resolver_indices.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_resolver_indices.py`:

```python
"""Unit tests for SymbolIndex and IndexBuilder."""

from cgis.core.models import Node, NodeNamespace, NodeType
from cgis.resolver.indices import IndexBuilder


def _node(
    fqn: str,
    node_type: NodeType = NodeType.FUNCTION,
    file_path: str = "mod.py",
    metadata: dict[str, object] | None = None,
) -> Node:
    """Build a minimal Node for index tests."""
    return Node(
        id=fqn,
        type=node_type,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=2,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# IndexBuilder.build
# ---------------------------------------------------------------------------


def test_build_indexes_global_symbols() -> None:
    """FUNCTION and CLASS nodes land in global_symbols and file_global_symbols."""
    index = IndexBuilder().build(
        [_node("mod.f"), _node("mod.Cls", node_type=NodeType.CLASS)]
    )
    assert index.global_symbols["f"] == ["mod.f"]
    assert index.global_symbols["Cls"] == ["mod.Cls"]
    assert index.file_global_symbols[("mod.py", "f")] == ["mod.f"]


def test_build_indexes_class_methods() -> None:
    """METHOD nodes are indexed under their enclosing class FQN."""
    index = IndexBuilder().build([_node("mod.Cls.m", node_type=NodeType.METHOD)])
    assert index.class_methods["mod.Cls"]["m"] == "mod.Cls.m"


def test_build_indexes_variables_separately() -> None:
    """VARIABLE nodes go to variable_symbols and NEVER to global_symbols (#161 invariant)."""
    index = IndexBuilder().build([_node("mod.Dep", node_type=NodeType.VARIABLE)])
    assert index.variable_symbols["Dep"] == ["mod.Dep"]
    assert index.file_variable_symbols[("mod.py", "Dep")] == ["mod.Dep"]
    assert "Dep" not in index.global_symbols


def test_build_indexes_file_import_map() -> None:
    """FILE nodes contribute their import_map keyed by normalized path."""
    file_node = _node(
        "mod",
        node_type=NodeType.FILE,
        file_path="./mod.py",
        metadata={"import_map": {"Dep": "other.Dep"}},
    )
    index = IndexBuilder().build([file_node])
    assert index.file_imports["mod.py"] == {"Dep": "other.Dep"}
    assert index.external_roots == {"other"}


# ---------------------------------------------------------------------------
# SymbolIndex.map_to_node_fqn
# ---------------------------------------------------------------------------


def test_map_to_node_fqn_exact() -> None:
    """An FQN that is already a node id maps to itself."""
    index = IndexBuilder().build([_node("cgis.mod.f")])
    assert index.map_to_node_fqn("cgis.mod.f") == "cgis.mod.f"


def test_map_to_node_fqn_suffix() -> None:
    """A node with an extra layout prefix is found via the suffix map."""
    index = IndexBuilder().build([_node("src.cgis.mod.f")])
    assert index.map_to_node_fqn("cgis.mod.f") == "src.cgis.mod.f"


def test_map_to_node_fqn_strips_import_prefix() -> None:
    """An import with an extra package prefix resolves by stripping segments."""
    index = IndexBuilder().build([_node("mod.f")])
    assert index.map_to_node_fqn("extra.mod.f") == "mod.f"


def test_map_to_node_fqn_ambiguous_suffix_is_none() -> None:
    """Two nodes sharing a suffix make the lookup ambiguous: None."""
    index = IndexBuilder().build([_node("a.cgis.mod.f"), _node("b.cgis.mod.f")])
    assert index.map_to_node_fqn("cgis.mod.f") is None


# ---------------------------------------------------------------------------
# SymbolIndex.classify_fqn
# ---------------------------------------------------------------------------


def test_classify_fqn_namespaces() -> None:
    """classify_fqn distinguishes INTERNAL, STDLIB, EXTERNAL, and UNKNOWN roots."""
    file_node = _node(
        "pkg",
        node_type=NodeType.FILE,
        metadata={"import_map": {"Depends": "fastapi.Depends"}},
    )
    index = IndexBuilder().build([file_node, _node("pkg.mod.f")])
    assert index.classify_fqn("pkg.mod.f") is NodeNamespace.INTERNAL
    assert index.classify_fqn("os.path.join") is NodeNamespace.STDLIB
    assert index.classify_fqn("fastapi.Depends") is NodeNamespace.EXTERNAL
    assert index.classify_fqn("zzz.unknown") is NodeNamespace.UNKNOWN
    assert index.classify_fqn("self.method") is NodeNamespace.INTERNAL
    assert index.classify_fqn(".relative") is NodeNamespace.INTERNAL


# ---------------------------------------------------------------------------
# SymbolIndex.is_variable_node / normalized_file_path
# ---------------------------------------------------------------------------


def test_is_variable_node() -> None:
    """True only for an existing VARIABLE node."""
    index = IndexBuilder().build(
        [_node("mod.Dep", node_type=NodeType.VARIABLE), _node("mod.f")]
    )
    assert index.is_variable_node("mod.Dep") is True
    assert index.is_variable_node("mod.f") is False
    assert index.is_variable_node("mod.missing") is False


def test_normalized_file_path_from_node_and_fallback() -> None:
    """Known FQN uses the node's path; unknown falls back to edge_file_path; else None."""
    index = IndexBuilder().build([_node("mod.f", file_path="./pkg/mod.py")])
    assert index.normalized_file_path("mod.f", None) == "pkg/mod.py"
    assert index.normalized_file_path("missing", "./x/y.py") == "x/y.py"
    assert index.normalized_file_path("missing", None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_resolver_indices.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.resolver.indices'`

- [ ] **Step 3: Implement `src/cgis/resolver/indices.py`**

Bodies of `map_to_node_fqn`, `classify_fqn`, `is_variable_node`, `normalized_file_path`, the build loop, `_add_node_to_suffix_map`, and `_build_external_roots` are moved **verbatim** from today's `engine.py` (attribute renames only):

```python
"""Symbol indices for FQN resolution: built once by IndexBuilder, read-only after."""

import builtins
import os
import sys
from dataclasses import dataclass

from cgis.core.models import Node, NodeNamespace, NodeType

_BUILTINS: frozenset[str] = frozenset(dir(builtins))
_SELF_PREFIX = "self."
_RAW_CLASS_PREFIX = "raw_class:"


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
        if fqn.startswith((".", _SELF_PREFIX)):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_resolver_indices.py -q`
Expected: 11 passed

- [ ] **Step 5: Run the FULL unit suite + type check**

Run: `uv run pytest tests/unit/ -q && make type-check`
Expected: all pass (engine.py untouched so far — old and new code coexist)

- [ ] **Step 6: Commit**

```bash
git add src/cgis/resolver/indices.py tests/unit/test_resolver_indices.py
git commit -m "refactor(resolver): add SymbolIndex + IndexBuilder (#115 task 1)"
```

---

### Task 2: `symbols.py` — SymbolResolver (TDD)

**Files:**
- Create: `src/cgis/resolver/symbols.py`
- Create: `tests/unit/test_resolver_symbols.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_resolver_symbols.py`:

```python
"""Unit tests for SymbolResolver (direct, without the ResolverEngine facade)."""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.resolver.indices import IndexBuilder
from cgis.resolver.symbols import SymbolResolver


def _node(fqn: str, node_type: NodeType, file_path: str = "mod.py") -> Node:
    """Build a minimal Node for resolver tests."""
    return Node(
        id=fqn,
        type=node_type,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=2,
    )


def _resolver(nodes: list[Node], edges: list[Edge] | None = None) -> SymbolResolver:
    """Build a SymbolResolver over a fresh index."""
    return SymbolResolver(IndexBuilder().build(nodes), edges or [])


def test_resolve_self_call_via_inheritance() -> None:
    """A self.method call on a child class resolves to the parent's method."""
    nodes = [
        _node("mod.Base", NodeType.CLASS),
        _node("mod.Base.greet", NodeType.METHOD),
        _node("mod.Child", NodeType.CLASS),
        _node("mod.Child.run", NodeType.METHOD),
    ]
    extends = Edge(
        id="x1", source="mod.Child", target="raw_class:Base", type=EdgeType.EXTENDS
    )
    resolver = _resolver(nodes, [extends])
    assert resolver.resolve_self_call("mod.Child.run", "greet") == "mod.Base.greet"


def test_resolve_global_call_prefers_same_file() -> None:
    """Ambiguous global names prefer the candidate defined in the caller's file."""
    nodes = [
        _node("a.helper", NodeType.FUNCTION, file_path="a.py"),
        _node("b.helper", NodeType.FUNCTION, file_path="b.py"),
        _node("a.caller", NodeType.FUNCTION, file_path="a.py"),
    ]
    resolver = _resolver(nodes)
    assert resolver.resolve_global_call("helper", "a.caller") == "a.helper"


def test_resolve_dep_candidate_import_is_authoritative() -> None:
    """An explicitly imported non-VARIABLE symbol returns None (no global fallback)."""
    file_node = Node(
        id="consumer",
        type=NodeType.FILE,
        name="consumer",
        file_path="consumer.py",
        start_line=1,
        end_line=1,
        metadata={"import_map": {"Dep": "models.Dep"}},
    )
    nodes = [
        file_node,
        _node("models.Dep", NodeType.CLASS, file_path="models.py"),
        _node("other.Dep", NodeType.VARIABLE, file_path="other.py"),
        _node("consumer.handler", NodeType.FUNCTION, file_path="consumer.py"),
    ]
    resolver = _resolver(nodes)
    assert resolver.resolve_dep_candidate("Dep", "consumer.handler", None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_resolver_symbols.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.resolver.symbols'`

- [ ] **Step 3: Implement `src/cgis/resolver/symbols.py`**

All bodies verbatim from today's `engine.py` with exactly these substitutions: `self._global_symbols` → `self._index.global_symbols` (same for the other 9 index fields), `self.nodes` → `self._index.nodes`, `self._map_to_node_fqn(...)` → `self._index.map_to_node_fqn(...)`, `self._get_normalized_file_path(...)` → `self._index.normalized_file_path(...)`, `self._is_variable_node(...)` → `self._index.is_variable_node(...)`, and the four promoted methods drop their leading underscore:

```python
"""Symbol resolution strategies over a SymbolIndex."""

from cgis.core.models import Edge, EdgeType, Node
from cgis.resolver.indices import _RAW_CLASS_PREFIX, SymbolIndex


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_resolver_symbols.py -q`
Expected: 3 passed

- [ ] **Step 5: Run the FULL unit suite + type check**

Run: `uv run pytest tests/unit/ -q && make type-check`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/cgis/resolver/symbols.py tests/unit/test_resolver_symbols.py
git commit -m "refactor(resolver): add SymbolResolver over SymbolIndex (#115 task 2)"
```

---

### Task 3: Rewrite `engine.py` as the 7-method facade

**Files:**
- Rewrite: `src/cgis/resolver/engine.py` (full replacement)
- Test: `tests/unit/test_resolver.py` — **must pass with ZERO edits** (acceptance criterion)

- [ ] **Step 1: Replace `engine.py` entirely with the facade**

The `resolve` loop, `_resolved_*_edge` bodies, `_ensure_virtual_node`, and `_make_virtual_node` move verbatim; resolution calls now delegate to `self._resolver.*`, node/classify lookups to `self._index.*`:

```python
"""Implements ResolverEngine class."""

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, Node, NodeNamespace, NodeType
from cgis.resolver.indices import _RAW_CLASS_PREFIX, _SELF_PREFIX, IndexBuilder
from cgis.resolver.symbols import SymbolResolver

RAW_DEP_PREFIX = "raw_dep:"


class ResolverEngine:
    """
    The 'Brain' of the CGIS.
    Transforms 'raw' semantic edges into resolved, high-confidence edges.

    Thin facade: IndexBuilder builds the SymbolIndex, SymbolResolver maps raw
    names to FQNs, and this class keeps edge finalization — confidence policy,
    edge rewrites, and virtual-node creation (spec §2.5).
    """

    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        """Build the symbol index and resolver from the extracted graph."""
        self.edges = edges
        self._index = IndexBuilder().build(nodes)
        self._resolver = SymbolResolver(self._index, edges)

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

        Strips the raw_class: prefix, resolves via SymbolResolver, then
        returns a copy of the edge with the resolved target (confidence 1.0) or
        the bare name as fallback (confidence 0.5).
        """
        raw = edge.target.removeprefix(_RAW_CLASS_PREFIX)
        resolved = self._resolver.resolve_class_ref(raw, edge.source, edge.file_path)
        final_target = resolved or raw
        confidence = 1.0 if resolved else 0.5
        return edge.model_copy(update={"target": final_target, "confidence": confidence})

    def _resolved_call_edge(self, edge: Edge) -> Edge:
        """Resolve a raw_call: edge to its final call target FQN.

        Strips the raw_call: prefix, dispatches to resolve_self_call for
        self.* calls or resolve_global_call otherwise, then returns a copy of
        the edge with the resolved target and adjusted confidence:
        min(edge.confidence + 0.5, 1.0) on success, 0.8 on failure.
        """
        raw_name = edge.target.removeprefix("raw_call:")
        if raw_name.startswith(_SELF_PREFIX):
            new_target = self._resolver.resolve_self_call(
                edge.source, raw_name.removeprefix(_SELF_PREFIX)
            )
        else:
            new_target = self._resolver.resolve_global_call(raw_name, edge.source, edge.file_path)
        final_target = new_target or raw_name
        confidence = min(edge.confidence + 0.5, 1.0) if new_target else 0.8
        return edge.model_copy(update={"target": final_target, "confidence": confidence})

    def _resolved_dep_edge(self, edge: Edge) -> Edge | None:
        """Resolve a raw_dep: candidate edge, or None when it must be dropped (spec §3.3)."""
        dep_name = edge.target.removeprefix(RAW_DEP_PREFIX)
        dep_target = self._resolver.resolve_dep_candidate(dep_name, edge.source, edge.file_path)
        if dep_target is None:
            # Speculative candidate that is not a DI alias: drop the edge
            # entirely — raw_dep: must never leak into output (spec §3.3).
            return None
        return edge.model_copy(update={"target": dep_target, "confidence": 1.0})

    def _ensure_virtual_node(self, target: str, virtual_nodes: dict[str, Node]) -> None:
        """Create a virtual boundary node for target if it is not already in the graph."""
        if target not in self._index.nodes and target not in virtual_nodes:
            virtual_nodes[target] = self._make_virtual_node(target, self._index.classify_fqn(target))

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
```

Note: the old `import builtins / os / sys` lines are gone (those concerns moved to `indices.py`); the spec §3.3 docstring reference is to the #161 design doc, keep it as-is.

- [ ] **Step 2: Run the resolver suite — zero edits allowed**

Run: `uv run pytest tests/unit/test_resolver.py -q`
Expected: all pass. If ANY test fails, the move was not verbatim — fix `engine.py`/`symbols.py`/`indices.py`, never the tests.

- [ ] **Step 3: Run the FULL unit suite + type check + lint**

Run: `uv run pytest tests/unit/ -q && make type-check && make lint`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add src/cgis/resolver/engine.py
git commit -m "refactor(resolver): ResolverEngine becomes a 7-method facade (#115 task 3)"
```

---

### Task 4: Guardrails — God-Object baseline, self-parse pins, drift measurement

This task runs IMMEDIATELY after Task 3 (spec §3.3: measure before polish).

**Files:**
- Modify: `tests/self_parsing/test_architecture.py` (remove one `_KNOWN_GOD_OBJECTS` entry)
- Modify: `tests/self_parsing/test_self_parse.py` (add 5 `EXPECTED_NODES` entries)

- [ ] **Step 1: Remove ResolverEngine from the God-Object baseline**

In `tests/self_parsing/test_architecture.py`, delete this line from `_KNOWN_GOD_OBJECTS`:

```python
        "resolver.engine.ResolverEngine",  # multi-pass symbol resolution
```

- [ ] **Step 2: Add new units to EXPECTED_NODES**

In `tests/self_parsing/test_self_parse.py`, extend the `# resolver` block of `EXPECTED_NODES`:

```python
    # resolver
    _fqn("resolver/engine.py",             "ResolverEngine"),
    _fqn("resolver/engine.py",             "ResolverEngine", "resolve"),
    _fqn("resolver/indices.py",            "SymbolIndex"),
    _fqn("resolver/indices.py",            "IndexBuilder"),
    _fqn("resolver/indices.py",            "IndexBuilder", "build"),
    _fqn("resolver/symbols.py",            "SymbolResolver"),
    _fqn("resolver/symbols.py",            "SymbolResolver", "resolve_global_call"),
```

- [ ] **Step 3: Run the self-parsing suite (god-object + drift ratchets + pins)**

Run: `uv run pytest tests/self_parsing/ -q`
Expected: all pass. Failure modes and required reactions:
- `test_god_object_baseline_not_exceeded` fails naming a NEW class (e.g. `resolver.symbols.SymbolResolver`) → a unit exceeded 10 methods; restructure (move a helper to `SymbolIndex` or module level), do NOT add to the baseline.
- `test_python_domains_within_tolerance` fails for `resolution` → the new IMPORTS layer pushed drift past 0.40; restructure imports (spec §3.3), do NOT raise the tolerance.

- [ ] **Step 4: Record the measured drift (visibility)**

```bash
uv run cgis ingest src --source-root src -o /tmp/cgis115.db
uv run cgis drift --db /tmp/cgis115.db --patterns docs/ontology/patterns.yaml
```

Note the `resolution` domain score printed by the command — it goes into the PR description (before: ≈0.35 with IMPORTS layer excluded).

- [ ] **Step 5: Update the stale tolerance comment**

In `docs/ontology/patterns.yaml`, the `resolution` domain comment says `IMPORTS layer empty → excluded` — no longer true after the split. Update ONLY the comment (never the `drift_tolerance` value) with the newly measured score, e.g.:

```yaml
  - name: "resolution"
    fqn_prefix: "cgis.resolver"
    expected_pattern: pipeline_stage
    profile: python
    drift_tolerance: 0.40 # v2 measured ≈0.XX post-#115 split (IMPORTS layer now populated: engine→symbols→indices chain)
```

- [ ] **Step 6: Commit**

```bash
git add tests/self_parsing/test_architecture.py tests/self_parsing/test_self_parse.py docs/ontology/patterns.yaml
git commit -m "test(self): ResolverEngine exits god-object baseline; pin new resolver units (#115 task 4)"
```

---

### Task 5: Full gates + final verification

- [ ] **Step 1: Run every gate**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```

Expected: all green. `make pytest` runs unit + self-parsing. `doc-coverage` (interrogate ≥90%) covers the new modules' docstrings.

- [ ] **Step 2: Verify acceptance criteria from the spec**

```bash
# test_resolver.py untouched on this branch:
git diff main -- tests/unit/test_resolver.py   # expected: empty output
# ResolverEngine gone from the baseline:
grep -c "ResolverEngine" tests/self_parsing/test_architecture.py   # expected: 0
```

- [ ] **Step 3: Commit any formatting deltas**

```bash
git add -A && git diff --cached --quiet || git commit -m "chore: formatting (#115 task 5)"
```

---

## Final checklist (controller, before PR)

- [ ] `ResolverEngine` has exactly 7 methods, `SymbolResolver` 9, `SymbolIndex` 4, `IndexBuilder` 3
- [ ] `git diff main -- tests/unit/test_resolver.py` is empty
- [ ] `_KNOWN_GOD_OBJECTS` no longer contains `resolver.engine.ResolverEngine`
- [ ] Drift `resolution` score measured and recorded for the PR description; tolerance untouched
- [ ] Full gates green
- [ ] PR body links issue #115 with `Closes #115` and the spec doc
