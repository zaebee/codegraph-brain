"""Unit test cases for Resolver."""

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.resolver.engine import ResolverEngine


def _file_node(file_path: str, import_map: dict[str, str]) -> Node:
    """Helper: build a FILE node carrying an import_map."""
    fqn = file_path.replace("/", ".").removesuffix(".py")
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=file_path.rsplit("/", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=10,
        metadata={"import_map": import_map},
    )


def test_resolver_resolves_direct_call() -> None:
    """Check: If resolver is able to connect direct function call."""
    # 1. SETUP: Create two nodes and dirty edge
    nodes = [
        Node(
            id="app.main.func_a",
            type=NodeType.FUNCTION,
            name="func_a",
            file_path="main.py",
            start_line=1,
            end_line=5,
            language="python",
        ),
        Node(
            id="app.utils.func_b",
            type=NodeType.FUNCTION,
            name="func_b",
            file_path="utils.py",
            start_line=10,
            end_line=15,
            language="python",
        ),
    ]

    edges = [
        Edge(
            id="edge_1",
            source="app.main.func_a",
            target="raw_call:func_b",  # Dirty target
            type=EdgeType.CALLS,
            confidence=0.5,
        )
    ]

    # 2. ACTION: Run Resolver
    resolver = ResolverEngine(nodes, edges)
    resolved_edges, _ = resolver.resolve()

    # 3. ASSERT: Check, that target is "clean"
    target_edge = next(e for e in resolved_edges if e.id == "edge_1")
    assert target_edge.target == "app.utils.func_b"
    expected = 0.5
    assert target_edge.confidence > expected  # Should increase confidence


def test_resolver_resolves_self_call() -> None:
    """Check: if resovler is able to resolve self.method_b inside class."""
    nodes = [
        Node(
            id="app.service.UserService",
            type=NodeType.CLASS,
            name="UserService",
            file_path="service.py",
            start_line=1,
            end_line=20,
            language="python",
        ),
        Node(
            id="app.service.UserService.method_a",
            type=NodeType.METHOD,
            name="method_a",
            file_path="service.py",
            start_line=5,
            end_line=10,
            language="python",
        ),
        Node(
            id="app.service.UserService.method_b",
            type=NodeType.METHOD,
            name="method_b",
            file_path="service.py",
            start_line=12,
            end_line=15,
            language="python",
        ),
    ]

    edges = [
        Edge(
            id="edge_self",
            source="app.service.UserService.method_a",
            target="raw_call:self.method_b",  # Dirty target through self
            type=EdgeType.CALLS,
            confidence=0.5,
        )
    ]

    resolver = ResolverEngine(nodes, edges)
    resolved_edges, _ = resolver.resolve()

    target_edge = next(e for e in resolved_edges if e.id == "edge_self")
    assert target_edge.target == "app.service.UserService.method_b"
    expected = 0.5
    assert target_edge.confidence > expected


def test_resolver_same_file_preferred_over_cross_file() -> None:
    """When two files define the same name, the same-file candidate wins."""
    nodes = [
        Node(
            id="app.main.helper",
            type=NodeType.FUNCTION,
            name="helper",
            file_path="main.py",
            start_line=1,
            end_line=3,
        ),
        Node(
            id="app.utils.helper",
            type=NodeType.FUNCTION,
            name="helper",
            file_path="utils.py",
            start_line=1,
            end_line=3,
        ),
        Node(
            id="app.main.caller",
            type=NodeType.FUNCTION,
            name="caller",
            file_path="main.py",
            start_line=5,
            end_line=8,
        ),
    ]
    edges = [
        Edge(
            id="edge_1",
            source="app.main.caller",
            target="raw_call:helper",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="main.py",
        )
    ]

    resolver = ResolverEngine(nodes, edges)
    resolved, _ = resolver.resolve()

    target_edge = next(e for e in resolved if e.id == "edge_1")
    assert target_edge.target == "app.main.helper"


def test_resolver_leaves_ambiguous_same_file_duplicate_unresolved() -> None:
    """Two symbols with the same name in the same file → leave unresolved (ambiguous)."""
    nodes = [
        Node(
            id="app.mod.execute_v1",
            type=NodeType.FUNCTION,
            name="execute",
            file_path="mod.py",
            start_line=1,
            end_line=3,
        ),
        Node(
            id="app.mod.execute_v2",
            type=NodeType.FUNCTION,
            name="execute",
            file_path="mod.py",
            start_line=5,
            end_line=7,
        ),
        Node(
            id="app.mod.caller",
            type=NodeType.FUNCTION,
            name="caller",
            file_path="mod.py",
            start_line=10,
            end_line=12,
        ),
    ]
    edges = [
        Edge(
            id="edge_1",
            source="app.mod.caller",
            target="raw_call:execute",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="mod.py",
        )
    ]

    resolver = ResolverEngine(nodes, edges)
    resolved, _ = resolver.resolve()

    target_edge = next(e for e in resolved if e.id == "edge_1")
    # Ambiguous: resolver must not pick either candidate (creates virtual node instead)
    assert target_edge.target not in ("app.mod.execute_v1", "app.mod.execute_v2")


def test_resolver_resolves_class_instantiation() -> None:
    """Check: If resolver is able to resolve class instantiation calls."""
    nodes = [
        Node(
            id="app.main.func_a",
            type=NodeType.FUNCTION,
            name="func_a",
            file_path="main.py",
            start_line=1,
            end_line=5,
            language="python",
        ),
        Node(
            id="app.models.User",
            type=NodeType.CLASS,
            name="User",
            file_path="models.py",
            start_line=10,
            end_line=20,
            language="python",
        ),
    ]

    edges = [
        Edge(
            id="edge_instantiate",
            source="app.main.func_a",
            target="raw_call:User",
            type=EdgeType.CALLS,
            confidence=0.5,
        )
    ]

    resolver = ResolverEngine(nodes, edges)
    resolved_edges, _ = resolver.resolve()

    target_edge = next(e for e in resolved_edges if e.id == "edge_instantiate")
    assert target_edge.target == "app.models.User"
    assert target_edge.confidence > 0.5


# --- Import Graph Linking (Issue #13) tests ---


def _func_node(fqn: str, file_path: str) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FUNCTION,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=5,
    )


def test_resolver_direct_import_resolves_call() -> None:
    """from src.utils import helper → helper() resolves to src.utils.helper."""
    nodes = [
        _file_node("main.py", {"helper": "src.utils.helper"}),
        _func_node("src.utils.helper", "src/utils.py"),
        _func_node("main.caller", "main.py"),
    ]
    edges = [
        Edge(
            id="e1",
            source="main.caller",
            target="raw_call:helper",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="main.py",
        )
    ]
    resolver = ResolverEngine(nodes, edges)
    result, _ = resolver.resolve()
    assert next(e for e in result if e.id == "e1").target == "src.utils.helper"


def test_resolver_aliased_import_resolves_call() -> None:
    """from src.utils import helper as h → h() resolves to src.utils.helper."""
    nodes = [
        _file_node("main.py", {"h": "src.utils.helper"}),
        _func_node("src.utils.helper", "src/utils.py"),
        _func_node("main.caller", "main.py"),
    ]
    edges = [
        Edge(
            id="e1",
            source="main.caller",
            target="raw_call:h",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="main.py",
        )
    ]
    resolver = ResolverEngine(nodes, edges)
    result, _ = resolver.resolve()
    assert next(e for e in result if e.id == "e1").target == "src.utils.helper"


def test_resolver_module_prefixed_call_resolves() -> None:
    """import json → json.dumps() resolves to json.dumps (external, kept as-is)."""
    nodes = [
        _file_node("main.py", {"json": "json"}),
        _func_node("main.caller", "main.py"),
    ]
    edges = [
        Edge(
            id="e1",
            source="main.caller",
            target="raw_call:json.dumps",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="main.py",
        )
    ]
    resolver = ResolverEngine(nodes, edges)
    result, _ = resolver.resolve()
    edge = next(e for e in result if e.id == "e1")
    assert edge.target == "json.dumps"
    assert not edge.target.startswith("raw_call:")


def test_resolver_imports_edge_emitted() -> None:
    """FILE node with import_map is built during extraction (structural IMPORTS edge)."""
    extractor = PythonExtractor()
    code = "from cgis.pipeline import IngestionPipeline\n"
    _, edges = extractor.parse(code, "service.py")
    imports_edges = [e for e in edges if e.type == EdgeType.IMPORTS]
    assert any(e.target == "cgis.pipeline" for e in imports_edges)


def test_resolver_src_layout_normalization() -> None:
    """from cgis.pipeline import X with node src.cgis.pipeline.X in graph resolves correctly."""
    nodes = [
        _file_node("service.py", {"IngestionPipeline": "cgis.pipeline.IngestionPipeline"}),
        # Node lives under src.cgis prefix
        Node(
            id="src.cgis.pipeline.IngestionPipeline",
            type=NodeType.CLASS,
            name="IngestionPipeline",
            file_path="src/cgis/pipeline.py",
            start_line=1,
            end_line=20,
        ),
        _func_node("service.run", "service.py"),
    ]
    edges = [
        Edge(
            id="e1",
            source="service.run",
            target="raw_call:IngestionPipeline",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="service.py",
        )
    ]
    resolver = ResolverEngine(nodes, edges)
    result, _ = resolver.resolve()
    assert next(e for e in result if e.id == "e1").target == "src.cgis.pipeline.IngestionPipeline"


def test_resolver_suffix_map_takes_priority_over_strip() -> None:
    """suffix_map (layout prefix) is checked before strip leading segments.

    imported_fqn="a.b.c", graph has node "b.c" (ambiguous strip candidate)
    AND "src.a.b.c" (the actual target via suffix_map).
    Without priority swap, strip("a.b.c") → "b.c" (wrong).
    """
    nodes = [
        # import_map: `from a.b import c as Target` → {"Target": "a.b.c"}
        _file_node("service.py", {"Target": "a.b.c"}),
        Node(
            id="b.c",
            type=NodeType.MODULE,
            name="c",
            file_path="b/c.py",
            start_line=1,
            end_line=1,
        ),
        Node(
            id="src.a.b.c",
            type=NodeType.CLASS,
            name="c",
            file_path="src/a/b/c.py",
            start_line=1,
            end_line=10,
        ),
        _func_node("service.use", "service.py"),
    ]
    edges = [
        Edge(
            id="e1",
            source="service.use",
            target="raw_call:Target",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="service.py",
        )
    ]
    resolver = ResolverEngine(nodes, edges)
    result, _ = resolver.resolve()
    assert next(e for e in result if e.id == "e1").target == "src.a.b.c"


def test_resolver_wildcard_import_call_stays_unresolved() -> None:
    """from module import * → calls remain raw_call: (can't statically resolve wildcard)."""
    nodes = [
        _file_node("main.py", {}),  # wildcard leaves import_map empty
        _func_node("main.caller", "main.py"),
    ]
    edges = [
        Edge(
            id="e1",
            source="main.caller",
            target="raw_call:mystery_func",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="main.py",
        )
    ]
    resolver = ResolverEngine(nodes, edges)
    result, virtual_nodes = resolver.resolve()
    # Wildcard import → can't resolve → virtual EXTERNAL node created
    target_edge = next(e for e in result if e.id == "e1")
    assert target_edge.target == "mystery_func"
    assert any(n.id == "mystery_func" for n in virtual_nodes)


# ============================================================================
# Test suite: Local Type Propagation & Instance Method Resolution (Issue #12)
# ============================================================================


def _func_node_with_local_types(
    func_fqn: str, file_path: str, local_types: dict[str, str] | None = None
) -> Node:
    """Helper: build a FUNCTION node with optional local_types metadata."""
    module_path = file_path.replace("/", ".").removesuffix(".py")
    full_fqn = f"{module_path}.{func_fqn}"
    metadata = {}
    if local_types:
        metadata["local_types"] = local_types
    return Node(
        id=full_fqn,
        type=NodeType.FUNCTION,
        name=func_fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=10,
        language="python",
        metadata=metadata,
    )


def test_local_type_resolution_assignment() -> None:
    """Assignment: eng = Engine() → eng.execute() resolves to src.mod.Engine.execute."""
    code = """
import src.mod

class Engine:
    def execute(self):
        pass

def main():
    eng = Engine()
    eng.execute()  # Should resolve to src.mod.Engine.execute
"""
    nodes, edges = PythonExtractor().parse(code, "src/mod.py")
    resolver = ResolverEngine(nodes, edges)
    result, _ = resolver.resolve()
    assert any(e.source == "src.mod.main" and e.target == "src.mod.Engine.execute" for e in result)


def test_local_type_resolution_param_annotation() -> None:
    """Test 2: Parameter Annotation - def run(store: SQLiteStore) → store.get_nodes() resolves."""
    code = """
class SQLiteStore:
    def get_nodes(self):
        pass

def run(store: SQLiteStore):
    store.get_nodes()  # Should resolve to SQLiteStore.get_nodes
"""
    nodes, edges = PythonExtractor().parse(code, "cgis/storage/sqlite_store.py")
    resolver = ResolverEngine(nodes, edges)
    result, _ = resolver.resolve()
    assert any(
        e.source == "cgis.storage.sqlite_store.run"
        and e.target == "cgis.storage.sqlite_store.SQLiteStore.get_nodes"
        for e in result
    )


def test_local_type_resolution_union_type() -> None:
    """Test 3: Union Type Parsing - def f(node: Node | None) → node.method() resolves."""
    code = """
class Node:
    def model_dump(self):
        pass

def process(node: Node | None):
    node.model_dump()  # Should resolve to Node.model_dump
"""
    nodes, edges = PythonExtractor().parse(code, "cgis/core/models.py")
    resolver = ResolverEngine(nodes, edges)
    result, _ = resolver.resolve()
    assert any(
        e.source == "cgis.core.models.process" and e.target == "cgis.core.models.Node.model_dump"
        for e in result
    )


def test_local_type_resolution_external_dep() -> None:
    """External dep: console = Console() → console.print() resolves to rich.console.Console.print."""  # noqa: E501
    code = """
from rich.console import Console

def main():
    console = Console()
    console.print()  # Should resolve to rich.console.Console.print
"""
    nodes, edges = PythonExtractor().parse(code, "app/main.py")
    resolver = ResolverEngine(nodes, edges)
    result, _ = resolver.resolve()
    assert any(
        e.source == "app.main.main" and e.target == "rich.console.Console.print" for e in result
    )


def test_method_call_result_not_captured_as_local_type() -> None:
    """Method-call results must not be recorded as local types.

    `result = obj.method(...)` cannot have its return type inferred statically.
    Recording `obj.method` as a type leads to spurious targets like
    `module.obj.method.attr` being classified as INTERNAL.
    """
    code = """
class Store:
    def get_item(self):
        pass

def process(store: Store):
    item = store.get_item()   # result of method call — type unknown
    item.render()             # must stay UNKNOWN, not resolve to Store.get_item.render
"""
    nodes, edges = PythonExtractor().parse(code, "app/service.py")
    # item should have no local_type recorded
    func_node = next(n for n in nodes if n.id == "app.service.process")
    local_types = func_node.metadata.get("local_types", {})
    assert "item" not in local_types, (
        f"item should have no type (method-call result), got: {local_types.get('item')}"
    )
    # item.render() must stay unresolved, not spuriously INTERNAL
    resolver = ResolverEngine(nodes, edges)
    result, virtual_nodes = resolver.resolve()
    item_render = next((e for e in result if "item.render" in e.target), None)
    if item_render:
        vnode = next((n for n in virtual_nodes if n.id == item_render.target), None)
        assert vnode is None or vnode.namespace.value != "INTERNAL", (
            f"item.render should not be INTERNAL, got namespace={vnode and vnode.namespace}"
        )


def test_local_type_resolution_typing_optional_annotation() -> None:
    """typing.Optional[X] (module-prefixed wrapper) must extract X, not 'typing.Optional'."""
    code = """
import typing

class SQLiteStore:
    def save(self):
        pass

def run(store: typing.Optional[SQLiteStore]):
    store.save()
"""
    nodes, _edges = PythonExtractor().parse(code, "cgis/storage/sqlite_store.py")
    func_node = next(n for n in nodes if n.id == "cgis.storage.sqlite_store.run")
    local_types = func_node.metadata.get("local_types", {})
    assert local_types.get("store", "").endswith("SQLiteStore"), (
        f"Expected type ending in 'SQLiteStore', got {local_types.get('store')!r}"
    )


def test_local_type_resolution_nested_generic_annotation() -> None:
    """Optional[list[Node]] must not produce a malformed type string."""
    code = """
class Node:
    def dump(self):
        pass

def run(nodes: Optional[list[Node]]):
    pass
"""
    nodes, _edges = PythonExtractor().parse(code, "app/service.py")
    func_node = next(n for n in nodes if n.id == "app.service.run")
    local_types = func_node.metadata.get("local_types", {})
    # Inner type is "list[Node]" — after cleaning should be "list", not "list[Node" (malformed)
    stored = local_types.get("nodes", "")
    assert "[" not in stored, f"Malformed type string in local_types: {stored!r}"


def test_local_type_resolution_optional_annotation() -> None:
    """Optional[X] param annotations must record X as the type, not Optional."""
    code = """
class SQLiteStore:
    def save(self):
        pass

def run(store: Optional[SQLiteStore]):
    store.save()
"""
    nodes, edges = PythonExtractor().parse(code, "cgis/storage/sqlite_store.py")
    func_node = next(n for n in nodes if n.id == "cgis.storage.sqlite_store.run")
    local_types = func_node.metadata.get("local_types", {})
    assert local_types.get("store", "").endswith("SQLiteStore"), (
        f"Expected type ending in 'SQLiteStore', got {local_types.get('store')!r}"
    )

    resolver = ResolverEngine(nodes, edges)
    result, _ = resolver.resolve()
    assert any(
        e.source == "cgis.storage.sqlite_store.run"
        and e.target == "cgis.storage.sqlite_store.SQLiteStore.save"
        for e in result
    )


def test_local_type_resolution_module_prefixed_constructor() -> None:
    """module.ClassName() constructors should record the resolved FQN via import_map."""
    code = """
from app import models

def create():
    store = models.Store()
    store.save()
"""
    nodes, _edges = PythonExtractor().parse(code, "app/service.py")
    func_node = next(n for n in nodes if n.id == "app.service.create")
    local_types = func_node.metadata.get("local_types", {})
    assert "store" in local_types, f"Expected 'store' in local_types, got {local_types}"
    assert local_types["store"] == "app.models.Store"


def test_resolver_resolves_dotted_class_ref_via_global_symbols() -> None:
    """class Child(models.Base) resolves Base via global symbols when models not in import map."""
    nodes = [
        Node(
            id="pkg.models.Base",
            type=NodeType.CLASS,
            name="Base",
            file_path="pkg/models.py",
            start_line=1,
            end_line=5,
        ),
        Node(
            id="pkg.child.Child",
            type=NodeType.CLASS,
            name="Child",
            file_path="pkg/child.py",
            start_line=1,
            end_line=5,
        ),
    ]
    edges = [
        Edge(
            id="e_ext",
            source="pkg.child.Child",
            target="raw_class:models.Base",
            type=EdgeType.EXTENDS,
            confidence=1.0,
            file_path="pkg/child.py",
        ),
    ]
    resolver = ResolverEngine(nodes, edges)
    resolved_edges, _ = resolver.resolve()

    ext_edge = next(e for e in resolved_edges if e.id == "e_ext")
    assert ext_edge.target == "pkg.models.Base"


# --- Inheritance resolution tests ---


def test_resolver_resolves_inherited_method() -> None:
    """Child inherits Parent; self.run() in Child resolves to Parent.run."""
    nodes = [
        Node(
            id="mod.Parent",
            type=NodeType.CLASS,
            name="Parent",
            file_path="mod.py",
            start_line=1,
            end_line=5,
        ),
        Node(
            id="mod.Parent.run",
            type=NodeType.METHOD,
            name="run",
            file_path="mod.py",
            start_line=2,
            end_line=4,
        ),
        Node(
            id="mod.Child",
            type=NodeType.CLASS,
            name="Child",
            file_path="mod.py",
            start_line=7,
            end_line=12,
        ),
        Node(
            id="mod.Child.call_run",
            type=NodeType.METHOD,
            name="call_run",
            file_path="mod.py",
            start_line=8,
            end_line=11,
        ),
    ]
    edges = [
        Edge(
            id="e_extends",
            source="mod.Child",
            target="raw_class:Parent",
            type=EdgeType.EXTENDS,
            confidence=1.0,
            file_path="mod.py",
        ),
        Edge(
            id="e_call",
            source="mod.Child.call_run",
            target="raw_call:self.run",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="mod.py",
        ),
    ]
    resolver = ResolverEngine(nodes, edges)
    resolved_edges, _ = resolver.resolve()

    call_edge = next(e for e in resolved_edges if e.id == "e_call")
    assert call_edge.target == "mod.Parent.run"
    assert call_edge.confidence > 0.5


def test_resolver_resolves_extends_edge_to_fqn() -> None:
    """EXTENDS edge with raw_class: target is resolved to the actual class FQN."""
    nodes = [
        Node(
            id="pkg.base.Base",
            type=NodeType.CLASS,
            name="Base",
            file_path="pkg/base.py",
            start_line=1,
            end_line=5,
        ),
        Node(
            id="pkg.child.Child",
            type=NodeType.CLASS,
            name="Child",
            file_path="pkg/child.py",
            start_line=1,
            end_line=5,
        ),
        _file_node("pkg/child.py", {"Base": "pkg.base.Base"}),
    ]
    edges = [
        Edge(
            id="e_ext",
            source="pkg.child.Child",
            target="raw_class:Base",
            type=EdgeType.EXTENDS,
            confidence=1.0,
            file_path="pkg/child.py",
        ),
    ]
    resolver = ResolverEngine(nodes, edges)
    resolved_edges, _ = resolver.resolve()

    ext_edge = next(e for e in resolved_edges if e.id == "e_ext")
    assert ext_edge.target == "pkg.base.Base"
    assert ext_edge.confidence == pytest.approx(1.0)


def test_resolver_inherited_method_cycle_safe() -> None:
    """Circular inheritance (A extends B, B extends A) doesn't infinite-loop."""
    nodes = [
        Node(
            id="mod.A", type=NodeType.CLASS, name="A", file_path="mod.py", start_line=1, end_line=3
        ),
        Node(
            id="mod.B", type=NodeType.CLASS, name="B", file_path="mod.py", start_line=5, end_line=7
        ),
        Node(
            id="mod.A.method",
            type=NodeType.METHOD,
            name="method",
            file_path="mod.py",
            start_line=9,
            end_line=11,
        ),
        # B.caller is the method that calls self.method (inherited from A through the cycle)
        Node(
            id="mod.B.caller",
            type=NodeType.METHOD,
            name="caller",
            file_path="mod.py",
            start_line=13,
            end_line=15,
        ),
    ]
    edges = [
        Edge(
            id="e1",
            source="mod.A",
            target="raw_class:B",
            type=EdgeType.EXTENDS,
            confidence=1.0,
            file_path="mod.py",
        ),
        Edge(
            id="e2",
            source="mod.B",
            target="raw_class:A",
            type=EdgeType.EXTENDS,
            confidence=1.0,
            file_path="mod.py",
        ),
        # self.method called from B.caller — resolved via A through the cycle-safe traversal
        Edge(
            id="e3",
            source="mod.B.caller",
            target="raw_call:self.method",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="mod.py",
        ),
    ]
    resolver = ResolverEngine(nodes, edges)
    resolved_edges, _ = resolver.resolve()
    # must not raise; method should resolve via A
    call_edge = next(e for e in resolved_edges if e.id == "e3")
    assert call_edge.target == "mod.A.method"


# --- Coverage gap tests ---


def test_resolve_self_call_no_class_context() -> None:
    """source FQN with no '.' → _resolve_self_call returns None."""
    nodes = [
        Node(
            id="bare_func",
            type=NodeType.FUNCTION,
            name="bare_func",
            file_path="mod.py",
            start_line=1,
            end_line=2,
        )
    ]
    edges = [
        Edge(
            id="e1",
            source="bare_func",
            target="raw_call:self.run",
            type=EdgeType.CALLS,
            confidence=0.5,
        )
    ]
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    edge = next(e for e in resolved if e.id == "e1")
    assert edge.target == "self.run"  # unresolved, prefix stripped by raw_name


def test_resolve_method_hierarchy_cycle_no_method() -> None:
    """Circular inheritance where neither class has the method triggers cycle guard."""
    nodes = [
        Node(
            id="mod.A", type=NodeType.CLASS, name="A", file_path="mod.py", start_line=1, end_line=3
        ),
        Node(
            id="mod.B", type=NodeType.CLASS, name="B", file_path="mod.py", start_line=5, end_line=7
        ),
        Node(
            id="mod.A.go",
            type=NodeType.METHOD,
            name="go",
            file_path="mod.py",
            start_line=2,
            end_line=3,
        ),
    ]
    edges = [
        Edge(
            id="e1",
            source="mod.A",
            target="raw_class:B",
            type=EdgeType.EXTENDS,
            confidence=1.0,
            file_path="mod.py",
        ),
        Edge(
            id="e2",
            source="mod.B",
            target="raw_class:A",
            type=EdgeType.EXTENDS,
            confidence=1.0,
            file_path="mod.py",
        ),
        Edge(
            id="e3",
            source="mod.A.go",
            target="raw_call:self.missing",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="mod.py",
        ),
    ]
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    edge = next(e for e in resolved if e.id == "e3")
    # neither A nor B defines "missing"; cycle guard fires → stays unresolved
    assert edge.target == "self.missing"


def test_resolve_global_call_uses_edge_file_path_fallback() -> None:
    """_get_normalized_file_path falls back to edge file_path when source_fqn not in nodes."""
    nodes = [
        Node(
            id="mod.target_fn",
            type=NodeType.FUNCTION,
            name="target_fn",
            file_path="mod.py",
            start_line=1,
            end_line=3,
        ),
    ]
    edges = [
        Edge(
            id="e1",
            source="ghost.caller",
            target="raw_call:target_fn",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="mod.py",
        ),
    ]
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    edge = next(e for e in resolved if e.id == "e1")
    assert edge.target == "mod.target_fn"


def test_resolve_self_call_nested_function_finds_class() -> None:
    """self.method() inside a nested function resolves via the enclosing class, not the fn."""
    nodes = [
        Node(
            id="mod.MyClass",
            type=NodeType.CLASS,
            name="MyClass",
            file_path="mod.py",
            start_line=1,
            end_line=10,
        ),
        Node(
            id="mod.MyClass.run",
            type=NodeType.METHOD,
            name="run",
            file_path="mod.py",
            start_line=2,
            end_line=5,
        ),
        Node(
            id="mod.MyClass.process",
            type=NodeType.METHOD,
            name="process",
            file_path="mod.py",
            start_line=6,
            end_line=9,
        ),
        Node(
            id="mod.MyClass.process.inner",
            type=NodeType.FUNCTION,
            name="inner",
            file_path="mod.py",
            start_line=7,
            end_line=8,
        ),
    ]
    edges = [
        Edge(
            id="e_nested_call",
            source="mod.MyClass.process.inner",
            target="raw_call:self.run",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="mod.py",
        ),
    ]
    resolver = ResolverEngine(nodes, edges)
    resolved_edges, _ = resolver.resolve()

    call_edge = next(e for e in resolved_edges if e.id == "e_nested_call")
    assert call_edge.target == "mod.MyClass.run"
