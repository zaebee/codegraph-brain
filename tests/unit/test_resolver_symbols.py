"""Unit tests for SymbolResolver (direct, without the ResolverEngine facade)."""

from cgis.core.models import Edge, EdgeType, Node, NodeType
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
    return SymbolResolver(nodes, edges or [])


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


def _scoped_file(fqn: str, file_path: str, scope: str) -> Node:
    """A FILE node that declares a scope wider than itself, as a Go file does."""
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=1,
        metadata={"scope": scope},
    )


def test_resolve_global_call_prefers_the_same_scope() -> None:
    """Step 7 H2: the candidate in a sibling file of the caller's package wins."""
    nodes = [
        _scoped_file("srv.a_go", "srv/a.go", "srv"),
        _scoped_file("srv.b_go", "srv/b.go", "srv"),
        _node("srv.helper", NodeType.FUNCTION, file_path="srv/b.go"),
        _node("other.helper", NodeType.FUNCTION, file_path="other/x.go"),
        _node("srv.caller", NodeType.FUNCTION, file_path="srv/a.go"),
    ]
    resolver = _resolver(nodes)
    assert resolver.resolve_global_call("helper", "srv.caller") == "srv.helper"


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


def test_local_type_call_does_not_fabricate_missing_method() -> None:
    """`var.method` where the method node does not exist resolves to None (#183).

    Previously `_resolve_local_type_call` returned the fabricated `Class.method`
    FQN, which the engine treated as a successful resolve (+0.5 conf) and minted
    a virtual node for a method that does not exist.
    """
    source = Node(
        id="mod.caller",
        type=NodeType.FUNCTION,
        name="caller",
        file_path="mod.py",
        start_line=1,
        end_line=2,
        metadata={"local_types": {"store": "mod.Store"}},
    )
    nodes = [
        source,
        _node("mod.Store", NodeType.CLASS),
        _node("mod.Store.save", NodeType.METHOD),
    ]
    resolver = _resolver(nodes)
    # An existing method still resolves.
    assert resolver.resolve_global_call("store.save", "mod.caller") == "mod.Store.save"
    # A missing method must NOT fabricate "mod.Store.delete".
    assert resolver.resolve_global_call("store.delete", "mod.caller") is None


def test_unresolved_parent_does_not_false_resolve_via_bare_name() -> None:
    """An EXTENDS parent that does not resolve must not leak a bare name into the
    inheritance tree and false-match an unrelated same-named class's method (#183)."""
    nodes = [
        _node("app.Child", NodeType.CLASS, file_path="app.py"),
        _node("app.Child.run", NodeType.METHOD, file_path="app.py"),
        # Unrelated method whose class segment is the bare name "Ghost".
        _node("Ghost.handle", NodeType.METHOD, file_path="other.py"),
    ]
    extends = Edge(id="e", source="app.Child", target="raw_class:Ghost", type=EdgeType.EXTENDS)
    resolver = _resolver(nodes, [extends])
    # "Ghost" never resolved (no Ghost class) → handle must NOT bind to Ghost.handle.
    assert resolver.resolve_self_call("app.Child.run", "handle") is None


def test_resolve_dep_candidate_prefers_the_same_scope() -> None:
    """Step 7 H2, the DI-alias half: a sibling file's VARIABLE of the caller's package wins."""
    nodes = [
        _scoped_file("srv.a_go", "srv/a.go", "srv"),
        _scoped_file("srv.b_go", "srv/b.go", "srv"),
        _node("srv.Dep", NodeType.VARIABLE, file_path="srv/b.go"),
        _node("other.Dep", NodeType.VARIABLE, file_path="other/x.go"),
        _node("srv.handler", NodeType.FUNCTION, file_path="srv/a.go"),
    ]
    resolver = _resolver(nodes)
    assert resolver.resolve_dep_candidate("Dep", "srv.handler", "srv/a.go") == "srv.Dep"
