"""Unit test cases for Resolver."""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.resolver.engine import ResolverEngine


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
