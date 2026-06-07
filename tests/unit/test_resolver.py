"""Unit test cases for Resolver."""

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
    resolved_edges = resolver.resolve()

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
    resolved_edges = resolver.resolve()

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
    resolved = resolver.resolve()

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
    resolved = resolver.resolve()

    target_edge = next(e for e in resolved if e.id == "edge_1")
    assert target_edge.target.startswith("raw_call:"), (
        "Ambiguous same-file duplicate must stay unresolved"
    )


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
    resolved_edges = resolver.resolve()

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
    result = resolver.resolve()
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
    result = resolver.resolve()
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
    result = resolver.resolve()
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
    result = resolver.resolve()
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
    result = resolver.resolve()
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
    result = resolver.resolve()
    assert next(e for e in result if e.id == "e1").target.startswith("raw_call:")
