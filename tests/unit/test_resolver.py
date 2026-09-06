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


# ============================================================================
# Test suite: raw_dep: DI alias resolution (Issue #161 slice 1, Task 4)
# ============================================================================


def _alias_node(fqn: str, file_path: str) -> Node:
    """Helper: build a VARIABLE node representing a DI alias."""
    return Node(
        id=fqn,
        type=NodeType.VARIABLE,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=5,
        end_line=5,
    )


def _raw_dep_edge(source: str, name: str, file_path: str) -> Edge:
    """Helper: build a speculative raw_dep: candidate edge."""
    return Edge(
        id=f"{file_path}:rawdep_1",
        source=source,
        target=f"raw_dep:{name}",
        type=EdgeType.DEPENDS_ON,
        confidence=0.1,
        file_path=file_path,
    )


def test_raw_dep_resolves_to_same_file_variable() -> None:
    """A raw_dep: candidate matching a VARIABLE node in the graph is kept."""
    nodes = [
        _func_node("deps.resolve_published_owner", "deps.py"),
        _alias_node("deps.ResolvedOwnerDep", "deps.py"),
    ]
    edges = [_raw_dep_edge("deps.resolve_published_owner", "ResolvedOwnerDep", "deps.py")]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    assert len(resolved) == 1
    assert resolved[0].target == "deps.ResolvedOwnerDep"
    assert resolved[0].confidence == pytest.approx(1.0)


def test_raw_dep_resolves_cross_file_via_import_map() -> None:
    """A raw_dep: candidate resolves through the consuming file's import map."""
    nodes = [
        _file_node("routes.py", {"PublishedOwnerDep": "deps.PublishedOwnerDep"}),
        _func_node("routes.get_vehicle", "routes.py"),
        _alias_node("deps.PublishedOwnerDep", "deps.py"),
    ]
    edges = [_raw_dep_edge("routes.get_vehicle", "PublishedOwnerDep", "routes.py")]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    assert len(resolved) == 1
    assert resolved[0].target == "deps.PublishedOwnerDep"


def test_raw_dep_to_class_becomes_a_references_edge() -> None:
    """A raw_dep: candidate resolving to an internal CLASS node becomes REFERENCES (D4).

    Was "is dropped" before Task 5: a non-VARIABLE resolution used to be
    discarded outright. It is now the annotation-edge path — an internal
    class candidate survives as a REFERENCES edge instead of a DEPENDS_ON
    wiring edge, so a type that is only ever referenced (never a DI alias
    and never called) still has an incoming edge in the graph.
    """
    nodes = [
        _func_node("routes.get_vehicle", "routes.py"),
        Node(
            id="models.User",
            type=NodeType.CLASS,
            name="User",
            file_path="models.py",
            start_line=1,
            end_line=5,
        ),
    ]
    edges = [_raw_dep_edge("routes.get_vehicle", "User", "routes.py")]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    assert len(resolved) == 1
    assert resolved[0].type == EdgeType.REFERENCES
    assert resolved[0].source == "routes.get_vehicle"
    assert resolved[0].target == "models.User"
    assert resolved[0].confidence == pytest.approx(1.0)


def test_raw_dep_unresolved_is_dropped_and_never_leaks() -> None:
    """An unresolved raw_dep: target never appears in the resolved edge set."""
    nodes = [_func_node("routes.get_vehicle", "routes.py")]
    edges = [
        _raw_dep_edge("routes.get_vehicle", "TotallyUnknown", "routes.py"),
        Edge(
            id="edge_call",
            source="routes.get_vehicle",
            target="raw_call:unknown_fn",
            type=EdgeType.CALLS,
            confidence=0.5,
        ),
    ]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    assert not any(e.target.startswith("raw_dep:") for e in resolved)
    # raw_call: keeps unresolved targets (regression guard for the asymmetry)
    assert any(e.target == "unknown_fn" for e in resolved)


def test_raw_dep_ambiguous_across_files_is_dropped() -> None:
    """Two same-named aliases in different files, no import map: candidate is dropped."""
    nodes = [
        _func_node("routes.get_vehicle", "routes.py"),
        _alias_node("deps.OwnerDep", "deps.py"),
        _alias_node("other.OwnerDep", "other.py"),
    ]
    edges = [_raw_dep_edge("routes.get_vehicle", "OwnerDep", "routes.py")]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    assert resolved == []


def test_raw_dep_explicit_import_shadows_global_alias() -> None:
    """An explicitly imported non-VARIABLE name never falls back to a same-named alias elsewhere.

    Was "is dropped" before Task 5. `resolve_dep_candidate` still refuses to
    fall back to `other.User` (the same-named alias in another file) — that
    shadowing guard is unchanged. What changed is what happens to the
    candidate once dep-resolution gives up: it now resolves as an internal
    class via the same import map and survives as a REFERENCES edge to
    `models.User`, not `other.User` and not a DEPENDS_ON edge.
    """
    nodes = [
        _file_node("routes.py", {"User": "models.User"}),
        _func_node("routes.get_vehicle", "routes.py"),
        Node(
            id="models.User",
            type=NodeType.CLASS,
            name="User",
            file_path="models.py",
            start_line=1,
            end_line=5,
        ),
        _alias_node("other.User", "other.py"),
    ]
    edges = [_raw_dep_edge("routes.get_vehicle", "User", "routes.py")]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    assert len(resolved) == 1
    assert resolved[0].type == EdgeType.REFERENCES
    assert resolved[0].target == "models.User"


def test_variable_nodes_do_not_pollute_call_resolution() -> None:
    """VARIABLE nodes are not in _global_symbols: raw_call: never lands on them."""
    nodes = [
        _func_node("app.caller", "app.py"),
        _alias_node("deps.OwnerDep", "deps.py"),
    ]
    edges = [
        Edge(
            id="edge_call",
            source="app.caller",
            target="raw_call:OwnerDep",
            type=EdgeType.CALLS,
            confidence=0.5,
        )
    ]

    resolved, _ = ResolverEngine(nodes, edges).resolve()

    # Unresolved (kept as bare name), NOT resolved to deps.OwnerDep
    assert resolved[0].target == "OwnerDep"


# ---------------------------------------------------------------------------
# raw_import: resolution (#161 slice 2)
# ---------------------------------------------------------------------------


def _sym_node(fqn: str, file_path: str = "defs.py") -> Node:
    """Minimal FUNCTION node for symbol-import tests."""
    return Node(
        id=fqn,
        type=NodeType.FUNCTION,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=2,
    )


def _raw_import_edge(source: str, fqn: str) -> Edge:
    """IMPORTS_SYMBOL candidate edge as the extractor emits it."""
    return Edge(
        id=f"{source}:imports_symbol:{fqn}",
        source=source,
        target=f"raw_import:{fqn}",
        type=EdgeType.IMPORTS_SYMBOL,
        confidence=0.1,
        file_path="consumer.py",
    )


def test_raw_import_resolves_exact_node() -> None:
    """A raw_import: target that names an existing node resolves at confidence 1.0."""
    nodes = [_sym_node("defs.Router")]
    edges = [_raw_import_edge("consumer", "defs.Router")]
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    assert len(resolved) == 1
    assert resolved[0].target == "defs.Router"
    assert resolved[0].type == EdgeType.IMPORTS_SYMBOL
    assert resolved[0].confidence == pytest.approx(1.0)
    assert virtual == []


def test_raw_import_resolves_via_suffix_map() -> None:
    """A src/-layout prefix mismatch resolves through the suffix map."""
    nodes = [_sym_node("src.defs.Router")]
    edges = [_raw_import_edge("consumer", "defs.Router")]
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    assert resolved[0].target == "src.defs.Router"


def test_raw_import_unknown_symbol_is_dropped() -> None:
    """External symbols (no node) drop the edge entirely — and mint NO virtual node."""
    edges = [_raw_import_edge("consumer", "fastapi.Depends")]
    resolved, virtual = ResolverEngine([], edges).resolve()
    assert resolved == []
    assert virtual == []


def test_raw_import_never_leaks_into_output() -> None:
    """No-leak negative: no output edge may carry the raw_import: prefix.

    Regression guard for the §2.4 dispatch-placement constraint — if the
    raw_import: branch lands after the passthrough catch-all, this fails.
    """
    nodes = [_sym_node("defs.Router")]
    edges = [
        _raw_import_edge("consumer", "defs.Router"),
        _raw_import_edge("consumer", "totally.unknown.Symbol"),
    ]
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    assert all(not e.target.startswith("raw_import:") for e in resolved)
    assert all(not v.id.startswith("raw_import:") for v in virtual)


# ---------------------------------------------------------------------------
# Receiver resolution: self.<attr>.<method>() (#414, spec D7/D8)
# ---------------------------------------------------------------------------


def _resolve_two(path_a: str, code_a: str, path_b: str, code_b: str) -> list[Edge]:
    """Parse two modules together and return the resolved edges."""
    extractor = PythonExtractor()
    nodes_a, edges_a = extractor.parse(code_a, path_a)
    nodes_b, edges_b = extractor.parse(code_b, path_b)
    resolved, _ = ResolverEngine(nodes_a + nodes_b, edges_a + edges_b).resolve()
    return resolved


_CLIENT = "class SearchClient:\n    def search(self, index):\n        return {}\n"


def test_self_attribute_call_resolves_through_the_declared_type() -> None:
    """`self.client.search()` resolves to SearchClient.search via self_types."""
    adapter = (
        "from pkg.client import SearchClient\n"
        "class Adapter:\n"
        "    def __init__(self, client: SearchClient) -> None:\n"
        "        self.client = client\n"
        "    def search(self, index):\n"
        "        return self.client.search(index)\n"
    )
    resolved = _resolve_two("pkg/client.py", _CLIENT, "pkg/adapter.py", adapter)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.adapter.Adapter.search", "pkg.client.SearchClient.search") in calls


def test_self_attribute_call_to_a_method_that_does_not_exist_is_not_invented() -> None:
    """A phantom method on an internal receiver must not resolve (spec D7).

    This is the bug class #414 was filed from: a call that mypy silenced with a
    type: ignore and that would have raised AttributeError on first use.
    """
    adapter = (
        "from pkg.client import SearchClient\n"
        "class Adapter:\n"
        "    def __init__(self, client: SearchClient) -> None:\n"
        "        self.client = client\n"
        "    def broken(self):\n"
        "        return self.client.no_such_method()\n"
    )
    resolved = _resolve_two("pkg/client.py", _CLIENT, "pkg/adapter.py", adapter)
    targets = {e.target for e in resolved if e.source == "pkg.adapter.Adapter.broken"}
    assert "pkg.client.SearchClient.no_such_method" not in targets


def test_self_attribute_call_finds_an_inherited_method() -> None:
    """Resolution walks EXTENDS, reusing the existing hierarchy search (spec D7)."""
    base = "class Base:\n    def ping(self):\n        return 1\nclass Child(Base):\n    pass\n"
    user = (
        "from pkg.base import Child\n"
        "class User:\n"
        "    def __init__(self, dep: Child) -> None:\n"
        "        self.dep = dep\n"
        "    def go(self):\n"
        "        return self.dep.ping()\n"
    )
    resolved = _resolve_two("pkg/base.py", base, "pkg/user.py", user)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.user.User.go", "pkg.base.Base.ping") in calls


def test_two_classes_with_a_same_named_attribute_do_not_share_a_target() -> None:
    """The placeholder collision: one attribute name, two types, two targets.

    Real instance on this repo: self._parser.parse was one graph vertex for
    PythonExtractor and TypeScriptExtractor, whose parsers are for different
    languages.
    """
    parsers = (
        "class PyParser:\n    def parse(self, s):\n        return s\n"
        "class TsParser:\n    def parse(self, s):\n        return s\n"
    )
    users = (
        "from pkg.parsers import PyParser, TsParser\n"
        "class PyHandler:\n"
        "    def __init__(self, parser: PyParser) -> None:\n"
        "        self.parser = parser\n"
        "    def go(self):\n"
        "        return self.parser.parse('x')\n"
        "class TsHandler:\n"
        "    def __init__(self, parser: TsParser) -> None:\n"
        "        self.parser = parser\n"
        "    def go(self):\n"
        "        return self.parser.parse('x')\n"
    )
    resolved = _resolve_two("pkg/parsers.py", parsers, "pkg/users.py", users)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.users.PyHandler.go", "pkg.parsers.PyParser.parse") in calls
    assert ("pkg.users.TsHandler.go", "pkg.parsers.TsParser.parse") in calls


def test_unannotated_receiver_is_left_alone() -> None:
    """No annotation, no guess — the edge keeps its placeholder (spec D1).

    Guard, not a regression test: this passes before and after the change.
    """
    code = (
        "class Adapter:\n"
        "    def __init__(self, client) -> None:\n"
        "        self.client = client\n"
        "    def go(self):\n"
        "        return self.client.search()\n"
    )
    nodes, edges = PythonExtractor().parse(code, "pkg/adapter.py")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    targets = {e.target for e in resolved if e.source == "pkg.adapter.Adapter.go"}
    assert "self.client.search" in targets


def test_deeper_chain_is_out_of_scope() -> None:
    """`self.a.b.c()` keeps its placeholder — two segments only (spec D8).

    Guard, not a regression test: resolving it would need the return type of
    self.client.search(), which nothing records.
    """
    adapter = (
        "from pkg.client import SearchClient\n"
        "class Adapter:\n"
        "    def __init__(self, client: SearchClient) -> None:\n"
        "        self.client = client\n"
        "    def go(self):\n"
        "        return self.client.search('i').first()\n"
    )
    resolved = _resolve_two("pkg/client.py", _CLIENT, "pkg/adapter.py", adapter)
    targets = {e.target for e in resolved if e.source == "pkg.adapter.Adapter.go"}
    assert any(t.startswith("self.client.search") for t in targets), targets


def test_plain_self_method_call_still_resolves() -> None:
    """The pre-existing `self.helper()` path must not regress.

    Guard: the dotted form is an added branch, not a replacement.
    """
    code = (
        "class Thing:\n"
        "    def helper(self):\n        return 1\n"
        "    def go(self):\n        return self.helper()\n"
    )
    nodes, edges = PythonExtractor().parse(code, "pkg/mod.py")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.mod.Thing.go", "pkg.mod.Thing.helper") in calls


def test_call_on_a_third_party_receiver_resolves_to_the_library() -> None:
    """`self._parser.parse()` on a tree_sitter.Parser is a real call into a dependency.

    The method has no node in our graph, but the receiver's type is EXTERNAL, so
    the target is kept and the engine mints a boundary node. Dropping it would
    make every library call through an injected collaborator invisible; that is
    the other half of the policy _resolve_local_type_call already applies to
    local variables (spec D7).
    """
    code = (
        "from tree_sitter import Parser\n"
        "class Extractor:\n"
        "    def __init__(self, parser: Parser) -> None:\n"
        "        self._parser = parser\n"
        "    def go(self, code):\n"
        "        return self._parser.parse(code)\n"
    )
    nodes, edges = PythonExtractor().parse(code, "pkg/mod.py")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    targets = {e.target for e in resolved if e.source == "pkg.mod.Extractor.go"}
    assert "tree_sitter.Parser.parse" in targets


def test_phantom_method_on_an_internal_receiver_is_still_dropped() -> None:
    """The external branch must not weaken the internal one.

    A method that does not exist on an internal class stays unresolved — that is
    the AttributeError-shaped bug #414 exists to surface.
    """
    client = "class SearchClient:\n    def search(self, i):\n        return {}\n"
    adapter = (
        "from pkg.client import SearchClient\n"
        "class Adapter:\n"
        "    def __init__(self, client: SearchClient) -> None:\n"
        "        self.client = client\n"
        "    def broken(self):\n"
        "        return self.client.search_available_vehicles()\n"
    )
    resolved = _resolve_two("pkg/client.py", client, "pkg/adapter.py", adapter)
    targets = {e.target for e in resolved if e.source == "pkg.adapter.Adapter.broken"}
    assert "pkg.client.SearchClient.search_available_vehicles" not in targets


def test_builtin_container_receiver_does_not_resolve() -> None:
    """`self._map: dict[str, int]` records the container, which names no class.

    self_types stores `dict` module-qualified (spec D1), so it has no node and
    classifies INTERNAL — correctly leaving `self._map.get()` alone rather than
    inventing a method on a fabricated FQN.
    """
    code = (
        "class Holder:\n"
        "    def __init__(self) -> None:\n"
        "        self._map: dict[str, int] = {}\n"
        "    def go(self):\n"
        "        return self._map.get('k')\n"
    )
    nodes, edges = PythonExtractor().parse(code, "pkg/mod.py")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    targets = {e.target for e in resolved if e.source == "pkg.mod.Holder.go"}
    assert "self._map.get" in targets


def test_inherited_attribute_resolves_from_the_base_class() -> None:
    """The collaborator is injected on a base and used from a subclass (F3).

    ReservationCore.__init__ takes the client; ReservationCreation calls
    self.reservation_client.create(). Looking only at the subclass's own
    self_types missed every such call — five real sites on owner-api.
    """
    client = "class Client:\n    def read(self, i):\n        return i\n"
    svc = (
        "from pkg.client import Client\n"
        "class Core:\n"
        "    def __init__(self, client: Client) -> None:\n"
        "        self.client = client\n"
        "class Creation(Core):\n"
        "    def go(self):\n"
        "        return self.client.read(1)\n"
    )
    resolved = _resolve_two("pkg/client.py", client, "pkg/svc.py", svc)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.svc.Creation.go", "pkg.client.Client.read") in calls


def test_phantom_method_on_a_first_party_receiver_is_not_fabricated() -> None:
    """A project class whose module is in the graph is ours, whatever classify_fqn says.

    classify_fqn judges by root string and external_roots is built from
    import-map values, so a first-party root can classify EXTERNAL — on
    owner-api that minted 131 confident edges onto app.* nodes that do not
    exist. A phantom method on our own class must stay unresolved (D7).
    """
    client = "class Client:\n    def read(self, i):\n        return i\n"
    svc = (
        "from pkg.client import Client\n"
        "class Svc:\n"
        "    def __init__(self, client: Client) -> None:\n"
        "        self.client = client\n"
        "    def go(self):\n"
        "        return self.client.no_such_method()\n"
    )
    resolved = _resolve_two("pkg/client.py", client, "pkg/svc.py", svc)
    targets = {e.target for e in resolved if e.source == "pkg.svc.Svc.go"}
    assert "pkg.client.Client.no_such_method" not in targets
    assert "self.client.no_such_method" in targets


def test_a_genuine_library_receiver_still_resolves() -> None:
    """The first-party guard must not close the external branch it protects.

    tree_sitter is not project code, so a method on it is a real dependency
    call and keeps its target.
    """
    code = (
        "from tree_sitter import Parser\n"
        "class Extractor:\n"
        "    def __init__(self, parser: Parser) -> None:\n"
        "        self._parser = parser\n"
        "    def go(self, code):\n"
        "        return self._parser.parse(code)\n"
    )
    nodes, edges = PythonExtractor().parse(code, "pkg/mod.py")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    targets = {e.target for e in resolved if e.source == "pkg.mod.Extractor.go"}
    assert "tree_sitter.Parser.parse" in targets


def test_multiple_inheritance_takes_the_leftmost_declaration() -> None:
    """`class C(Left, Right)` with the attribute on both resolves to Left's type.

    Python's MRO searches bases left to right. The ancestor walk is iterative
    over a LIFO stack, so extending it in source order pops the rightmost base
    first and silently answers with the wrong class — a wrong type in
    self_types, which is worse than a missing one because the call then resolves
    confidently to the wrong method.
    """
    clients = (
        "class LeftClient:\n    def ping(self):\n        return 'left'\n"
        "class RightClient:\n    def ping(self):\n        return 'right'\n"
    )
    rest = (
        "from pkg.clients import LeftClient, RightClient\n"
        "class LeftBase:\n"
        "    def __init__(self, c: LeftClient) -> None:\n"
        "        self.client = c\n"
        "class RightBase:\n"
        "    def __init__(self, c: RightClient) -> None:\n"
        "        self.client = c\n"
        "class Child(LeftBase, RightBase):\n"
        "    def go(self):\n"
        "        return self.client.ping()\n"
    )
    resolved = _resolve_two("pkg/clients.py", clients, "pkg/rest.py", rest)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.rest.Child.go", "pkg.clients.LeftClient.ping") in calls
    assert ("pkg.rest.Child.go", "pkg.clients.RightClient.ping") not in calls


# ---------------------------------------------------------------------------
# Re-export resolution (#417)
# ---------------------------------------------------------------------------


def _three(defs: tuple[str, str], init: tuple[str, str], use: tuple[str, str]) -> list[Edge]:
    """Parse a definer, an __init__ that re-exports it, and a consumer."""
    extractor = PythonExtractor()
    nodes: list[Node] = []
    edges: list[Edge] = []
    for path, code in (defs, init, use):
        n, e = extractor.parse(code, path)
        nodes += n
        edges += e
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    return resolved


_RATING = ("app/models/rating.py", "class Rating:\n    pass\n")
_CONSUMER = (
    "app/svc.py",
    "from app.models import Rating\n\n\ndef go(x: Rating) -> None:\n    pass\n",
)


def test_explicit_reexport_resolves_to_the_defining_module() -> None:
    """`from .rating import Rating` in __init__ lets a consumer reach the real class.

    The extractor already records this forwarding on the FILE node; the resolver
    never read it, so `from app.models import Rating` mapped to app.models.Rating,
    which has no node, and the edge died (#417 defect 1).
    """
    init = ("app/models/__init__.py", "from .rating import Rating\n")
    resolved = _three(_RATING, init, _CONSUMER)
    refs = {(e.source, e.target) for e in resolved if e.type == EdgeType.REFERENCES}
    assert ("app.svc.go", "app.models.rating.Rating") in refs


def test_star_reexport_resolves_to_the_defining_module() -> None:
    """`from .rating import *` — owner-api's actual shape (#417 defect 2).

    A star import contributes no names to the import map, so find_reexports had
    nothing to work from and fixing defect 1 alone would not have helped the one
    file that matters: app/models/__init__.py, with twelve of these.
    """
    init = ("app/models/__init__.py", "from .rating import *\n")
    resolved = _three(_RATING, init, _CONSUMER)
    refs = {(e.source, e.target) for e in resolved if e.type == EdgeType.REFERENCES}
    assert ("app.svc.go", "app.models.rating.Rating") in refs


def test_a_reexport_still_needs_someone_to_use_it() -> None:
    """Identity is not usage: forwarding a name is a mention, not a reader.

    An __init__ that imports a name, lists it in __all__ and reads it nowhere has
    used nothing. Counting the re-export as a use resurrects genuinely dead code
    — the owner-api author hit exactly this and it revived two adapters their
    #1343 had deleted.
    """
    init = (
        "app/models/__init__.py",
        'from .rating import Rating\n\n__all__ = ["Rating"]\n',
    )
    unused = ("app/other.py", "def unrelated() -> None:\n    pass\n")
    resolved = _three(_RATING, init, unused)
    users = {
        e.source
        for e in resolved
        if e.target == "app.models.rating.Rating"
        and e.type in (EdgeType.CALLS, EdgeType.REFERENCES)
    }
    assert not users, f"a re-export alone must not count as a use, but: {users}"


def test_multi_hop_reexport_reaches_the_definer() -> None:
    """A -> B -> C forwarding must not stop at B."""
    inner = ("app/models/rating.py", "class Rating:\n    pass\n")
    mid = ("app/models/__init__.py", "from .rating import *\n")
    outer = ("app/__init__.py", "from app.models import *\n")
    consumer = ("app/svc.py", "from app import Rating\n\n\ndef go(x: Rating) -> None:\n    pass\n")
    extractor = PythonExtractor()
    nodes: list[Node] = []
    edges: list[Edge] = []
    for path, code in (inner, mid, outer, consumer):
        n, e = extractor.parse(code, path)
        nodes += n
        edges += e
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    refs = {(e.source, e.target) for e in resolved if e.type == EdgeType.REFERENCES}
    assert ("app.svc.go", "app.models.rating.Rating") in refs


def test_a_private_name_is_not_star_reexported() -> None:
    """`import *` does not bring in a leading-underscore name."""
    private = ("app/models/rating.py", "class _Internal:\n    pass\n")
    init = ("app/models/__init__.py", "from .rating import *\n")
    consumer = (
        "app/svc.py",
        "from app.models import _Internal\n\n\ndef go(x: _Internal) -> None:\n    pass\n",
    )
    resolved = _three(private, init, consumer)
    refs = {(e.source, e.target) for e in resolved if e.type == EdgeType.REFERENCES}
    assert ("app.svc.go", "app.models.rating._Internal") not in refs


# ---------------------------------------------------------------------------
# Calls outside a function body (#416)
# ---------------------------------------------------------------------------


def test_module_level_construction_is_recorded() -> None:
    """A class built in a module-level registry is used, and must not read as dead.

    `_EXTRACTORS = {".py": PythonExtractor()}` produced no CALLS edge at all,
    because the walker dispatched a call node only when it had an enclosing
    function. Registry dicts, singletons and DI wiring are exactly the shape
    that assembles an application, and all of it was invisible.
    """
    ext = "class Extractor:\n    pass\n"
    reg = "from pkg.ext import Extractor\n\n_REGISTRY = {'py': Extractor()}\n"
    resolved = _resolve_two("pkg/ext.py", ext, "pkg/reg.py", reg)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.reg", "pkg.ext.Extractor") in calls


def test_class_body_call_is_attributed_to_the_class() -> None:
    """`x = Factory()` in a class body belongs to the class, not to the module."""
    fac = "class Factory:\n    pass\n"
    holder = "from pkg.fac import Factory\nclass Holder:\n    made = Factory()\n"
    resolved = _resolve_two("pkg/fac.py", fac, "pkg/holder.py", holder)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.holder.Holder", "pkg.fac.Factory") in calls
    assert ("pkg.holder", "pkg.fac.Factory") not in calls


def test_call_inside_a_module_level_lambda_is_recorded() -> None:
    """A lambda is not a def, so its body was invisible too.

    `build: lambda roots: Extractor(roots)` in a registry tuple is how this
    repository wires its own extractors, and the construction was lost.
    """
    ext = "class Extractor:\n    pass\n"
    reg = "from pkg.ext import Extractor\n\nBUILD = lambda r: Extractor()\n"
    resolved = _resolve_two("pkg/ext.py", ext, "pkg/reg.py", reg)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.reg", "pkg.ext.Extractor") in calls


def test_a_function_body_call_still_belongs_to_the_function() -> None:
    """Guard: the ordinary case must keep its own source, not slide to the module."""
    ext = "class Extractor:\n    pass\n"
    user = "from pkg.ext import Extractor\ndef build():\n    return Extractor()\n"
    resolved = _resolve_two("pkg/ext.py", ext, "pkg/user.py", user)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.user.build", "pkg.ext.Extractor") in calls
    assert ("pkg.user", "pkg.ext.Extractor") not in calls


# ---------------------------------------------------------------------------
# A class named in a load position is a reference (D10, #415)
# ---------------------------------------------------------------------------


def _refs(resolved: list[Edge]) -> set[tuple[str, str]]:
    """The (source, target) pairs of every REFERENCES edge."""
    return {(e.source, e.target) for e in resolved if e.type == EdgeType.REFERENCES}


_WIDGET = "class Widget:\n    pass\n"


def test_a_class_handed_to_a_call_is_referenced() -> None:
    """`app.add_middleware(Widget)` names Widget and never calls it.

    This is the single largest shape behind the orphan query's false positives
    on application code: a framework is handed the class object and constructs
    it itself, so no CALLS edge exists and the class reads as dead.
    """
    user = "from pkg.w import Widget\n\ndef setup(app):\n    app.add_middleware(Widget)\n"
    resolved = _resolve_two("pkg/w.py", _WIDGET, "pkg/user.py", user)
    assert ("pkg.user.setup", "pkg.w.Widget") in _refs(resolved)


def test_a_caught_exception_class_is_referenced() -> None:
    """`except Widget:` is a use — the only one an exception type usually gets."""
    user = (
        "from pkg.w import Widget\n\n"
        "def run():\n"
        "    try:\n"
        "        pass\n"
        "    except Widget:\n"
        "        pass\n"
    )
    resolved = _resolve_two("pkg/w.py", _WIDGET, "pkg/user.py", user)
    assert ("pkg.user.run", "pkg.w.Widget") in _refs(resolved)


def test_an_enum_named_only_as_a_member_access_is_referenced() -> None:
    """`Widget.SIZE` names Widget as the head of the attribute, not as a call."""
    user = "from pkg.w import Widget\n\ndef pick():\n    return Widget.SIZE\n"
    resolved = _resolve_two("pkg/w.py", _WIDGET, "pkg/user.py", user)
    assert ("pkg.user.pick", "pkg.w.Widget") in _refs(resolved)


def test_a_class_in_a_collection_literal_is_referenced() -> None:
    """A registry tuple names the class; nothing constructs it at that point."""
    user = "from pkg.w import Widget\n\nCHOICES = [('w', Widget)]\n"
    resolved = _resolve_two("pkg/w.py", _WIDGET, "pkg/user.py", user)
    assert ("pkg.user", "pkg.w.Widget") in _refs(resolved)


def test_a_referenced_function_produces_no_reference_edge() -> None:
    """D3 holds: only an internal CLASS survives, so a function handed to a call is dropped.

    Without this the rule would emit an edge for every callback, decorator
    argument and provider alias in the codebase.
    """
    lib = "def helper():\n    return 1\n"
    user = "from pkg.lib import helper\n\ndef go(app):\n    app.on_event(helper)\n"
    resolved = _resolve_two("pkg/lib.py", lib, "pkg/user.py", user)
    assert not [e for e in resolved if e.type == EdgeType.REFERENCES]
    assert not [e for e in resolved if e.target.startswith("raw_dep:")]


def test_a_constructed_class_still_gets_its_calls_edge_and_no_duplicate_reference() -> None:
    """The call's own function name is excluded — CALLS already says it."""
    user = "from pkg.w import Widget\n\ndef build():\n    return Widget()\n"
    resolved = _resolve_two("pkg/w.py", _WIDGET, "pkg/user.py", user)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.user.build", "pkg.w.Widget") in calls
    assert ("pkg.user.build", "pkg.w.Widget") not in _refs(resolved)


def test_an_unimported_local_name_is_not_a_reference() -> None:
    """The import_map gate: a local variable that happens to share a class name.

    Without the gate every `widget = ...` in the codebase would be a candidate,
    and a same-named class elsewhere would collect edges from code that has
    never heard of it.
    """
    other = "class Widget:\n    pass\n"
    user = "def run():\n    Widget = 1\n    return Widget\n"
    resolved = _resolve_two("pkg/w.py", other, "pkg/user.py", user)
    assert ("pkg.user.run", "pkg.w.Widget") not in _refs(resolved)


def test_a_class_defined_in_the_same_file_is_referenced_by_a_sibling() -> None:
    """The second gate: a same-file class needs no import to be named."""
    code = "class Widget:\n    pass\n\ndef register(app):\n    app.add_middleware(Widget)\n"
    resolved = _resolve_two("pkg/w.py", code, "pkg/other.py", "x = 1\n")
    assert ("pkg.w.register", "pkg.w.Widget") in _refs(resolved)


def test_the_member_half_of_an_attribute_is_not_a_candidate() -> None:
    """`thing.Widget` names an attribute, not necessarily the imported class.

    Only the head of an attribute chain is a bare name; the member half is a
    field lookup and emitting for it would attach edges through any object that
    happens to expose a same-named attribute.
    """
    user = "from pkg.w import Widget\n\ndef go(thing):\n    return thing.Widget\n"
    resolved = _resolve_two("pkg/w.py", _WIDGET, "pkg/user.py", user)
    assert ("pkg.user.go", "pkg.w.Widget") not in _refs(resolved)


def test_two_owners_whose_names_run_together_get_distinct_edges() -> None:
    """Edge ids are a PRIMARY KEY, so an ambiguous id silently drops an edge.

    `nameref_{owner}_{name}` made `handle_User` + `Session` and `handle` +
    `User_Session` produce one id, and INSERT OR REPLACE kept whichever came
    last. The separator has to be a character neither an FQN nor a Python name
    can contain.
    """
    lib = "class Session:\n    pass\n\n\nclass User_Session:\n    pass\n"
    user = (
        "from pkg.lib import Session, User_Session\n\n"
        "def handle_User(app):\n"
        "    app.add(Session)\n\n"
        "def handle(app):\n"
        "    app.add(User_Session)\n"
    )
    resolved = _resolve_two("pkg/lib.py", lib, "pkg/user.py", user)
    refs = _refs(resolved)
    assert ("pkg.user.handle_User", "pkg.lib.Session") in refs
    assert ("pkg.user.handle", "pkg.lib.User_Session") in refs
    ids = [e.id for e in resolved if e.type == EdgeType.REFERENCES]
    assert len(ids) == len(set(ids)), "two references collapsed onto one edge id"


def test_a_class_reached_through_its_module_alias_is_referenced() -> None:
    """`from pkg import mod` then `mod.Widget.build()` — the head is a module, not a class.

    Found by review: the candidate for the head alone resolves to a module and
    D3 drops it, so a class only ever reached this way stayed invisible. Two
    production classes in owner-api were reported dead for exactly this reason.
    """
    widget = "class Widget:\n    @classmethod\n    def build(cls):\n        return cls()\n"
    user = "from pkg import w\n\ndef go():\n    return w.Widget.build()\n"
    resolved = _resolve_two("pkg/w.py", widget, "pkg/user.py", user)
    assert ("pkg.user.go", "pkg.w.Widget") in _refs(resolved)


# ---------------------------------------------------------------------------
# Decorator expressions (#429)
# ---------------------------------------------------------------------------


def test_a_class_named_in_a_decorator_argument_is_referenced() -> None:
    """`@router.post(..., response_model=Schema)` names Schema and nothing else does.

    `_walk` hands a decorated definition to `_handle_decorated_definition`, which
    descends into the definition and never into the decorators, so every name in
    a decorator was invisible. On owner-api that is 117 names, mostly response
    schemas, each of which the orphan query would report as dead unless a return
    annotation happened to name it too.
    """
    schema = "class Schema:\n    pass\n"
    user = (
        "from pkg.s import Schema\n\n"
        "@router.post('/x', response_model=Schema)\n"
        "def handler():\n"
        "    pass\n"
    )
    resolved = _resolve_two("pkg/s.py", schema, "pkg/user.py", user)
    refs = {(e.source, e.target) for e in resolved if e.type == EdgeType.REFERENCES}
    assert ("pkg.user.handler", "pkg.s.Schema") in refs


def test_a_call_inside_a_decorator_is_attributed_to_the_decorated_function() -> None:
    """A decorator has no owner of its own, so the definition it decorates is it."""
    factory = "def build():\n    return 1\n"
    user = "from pkg.f import build\n\n@app.route(build())\ndef handler():\n    pass\n"
    resolved = _resolve_two("pkg/f.py", factory, "pkg/user.py", user)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.user.handler", "pkg.f.build") in calls


def test_a_decorated_class_gets_its_decorator_call_edge() -> None:
    """A decorated function records `raw_call:<decorator>`; a decorated class did not.

    `process_function_node` emits one CALLS edge per decorator name and
    `process_class_node`, given the same list, emitted none — so `@register`
    above a class was a use nothing recorded.
    """
    registry = "def register(cls):\n    return cls\n"
    user = "from pkg.r import register\n\n@register\nclass Held:\n    pass\n"
    resolved = _resolve_two("pkg/r.py", registry, "pkg/user.py", user)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.user.Held", "pkg.r.register") in calls


def test_a_decorator_depends_still_emits_no_dependency_edge() -> None:
    """The DI half of #429 is a spec decision, deliberately not taken here.

    `dependencies=[Depends(guard)]` guards the decorated function, but attributing
    a DEPENDS_ON edge to it would be the first place an edge's source differs
    from its lexical owner. Until that is written down, the decorator path emits
    CALLS and REFERENCES only — this test pins the boundary so the change is a
    decision rather than a side effect.
    """
    guard = "def guard():\n    return 1\n"
    user = (
        "from pkg.g import guard\n"
        "from fastapi import Depends\n\n"
        "@router.post('/x', dependencies=[Depends(guard)])\n"
        "def handler():\n"
        "    pass\n"
    )
    resolved = _resolve_two("pkg/g.py", guard, "pkg/user.py", user)
    assert not [e for e in resolved if e.type == EdgeType.DEPENDS_ON]


def test_a_function_body_is_still_walked_under_a_decorator() -> None:
    """Guard: adding the decorator pass must not disturb the definition pass."""
    widget = "class Widget:\n    pass\n"
    user = "from pkg.w import Widget\n\n@app.route('/x')\ndef handler():\n    return Widget()\n"
    resolved = _resolve_two("pkg/w.py", widget, "pkg/user.py", user)
    calls = {(e.source, e.target) for e in resolved if e.type == EdgeType.CALLS}
    assert ("pkg.user.handler", "pkg.w.Widget") in calls


def test_a_decorator_call_is_not_recorded_twice() -> None:
    """`process_function_node` already records the decorator's own name.

    Walking the decorator whole emitted `router.post` twice under two edge ids —
    once from the decorator list, once from the call node — and its name a third
    time as a REFERENCES. The walk covers the arguments only.
    """
    code = "@router.post('/x')\ndef handler():\n    pass\n"
    _nodes, edges = PythonExtractor().parse(code, "pkg/user.py")
    to_post = [e for e in edges if e.target == "raw_call:router.post"]
    assert len(to_post) == 1, [e.id for e in to_post]
    assert not [e for e in edges if e.target == "raw_dep:router"]
