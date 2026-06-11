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
    index = IndexBuilder().build([_node("mod.f"), _node("mod.Cls", node_type=NodeType.CLASS)])
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
    index = IndexBuilder().build([_node("mod.Dep", node_type=NodeType.VARIABLE), _node("mod.f")])
    assert index.is_variable_node("mod.Dep") is True
    assert index.is_variable_node("mod.f") is False
    assert index.is_variable_node("mod.missing") is False


def test_normalized_file_path_from_node_and_fallback() -> None:
    """Known FQN uses the node's path; unknown falls back to edge_file_path; else None."""
    index = IndexBuilder().build([_node("mod.f", file_path="./pkg/mod.py")])
    assert index.normalized_file_path("mod.f", None) == "pkg/mod.py"
    assert index.normalized_file_path("missing", "./x/y.py") == "x/y.py"
    assert index.normalized_file_path("missing", None) is None
