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
    extends = Edge(id="x1", source="mod.Child", target="raw_class:Base", type=EdgeType.EXTENDS)
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
