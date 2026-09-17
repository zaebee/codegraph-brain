"""`get_structure` on a package prefix (#487).

A package is not a node: it exists in the graph only if it has an `__init__.py`,
and even then containment runs file → symbol, so the node holds nothing. Rather
than mint PACKAGE nodes into every graph, the structural query answers a prefix
by listing the modules under it.

The discriminator is FILE nodes *strictly under* the id. A module has symbols
under it but no files, so `pkg.mod` keeps its ordinary answer.
"""

from pathlib import Path

import pytest

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeType
from cgis.query.engine import QueryEngine
from cgis.storage.sqlite_store import SQLiteStore


def _file(node_id: str) -> Node:
    return Node(
        id=node_id,
        type=NodeType.FILE,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path=f"{node_id.replace('.', '/')}.py",
        start_line=1,
        end_line=1,
    )


def _symbol(node_id: str, node_type: NodeType = NodeType.FUNCTION) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path=f"{node_id.rsplit('.', maxsplit=1)[0].replace('.', '/')}.py",
        start_line=3,
        end_line=4,
    )


def _graph() -> tuple[list[Node], list[Edge]]:
    """`app.api` is a package with two modules; `app.store` is a module with symbols."""
    nodes = [
        _file("app.api.routes"),
        _file("app.api.schemas"),
        _file("app.store"),
        _symbol("app.api.routes.get_user"),
        _symbol("app.store.save"),
        _symbol("app.store.Store", NodeType.CLASS),
    ]
    edges = [
        Edge(
            id="c1",
            source="app.api.routes",
            target="app.api.routes.get_user",
            type=EdgeType.CONTAINS,
        ),
        Edge(id="c2", source="app.store", target="app.store.save", type=EdgeType.CONTAINS),
        Edge(id="c3", source="app.store", target="app.store.Store", type=EdgeType.CONTAINS),
    ]
    return nodes, edges


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = str(tmp_path / "graph.db")
    with SQLiteStore(path) as store:
        store.save_graph(*_graph())
    return path


def _structure(db_path: str, fqn: str, depth: int = 1) -> tuple[list[Node], list[Edge]]:
    with SQLiteStore(db_path) as store:
        return QueryEngine(store).get_structural_graph(fqn, max_depth=depth)


def test_a_package_prefix_lists_its_modules(db: str) -> None:
    """`app.api` is no node at all, yet the question has an answer."""
    nodes, edges = _structure(db, "app.api")

    assert {node.id for node in nodes} == {"app.api", "app.api.routes", "app.api.schemas"}
    assert {(edge.source, edge.target) for edge in edges} == {
        ("app.api", "app.api.routes"),
        ("app.api", "app.api.schemas"),
    }


def test_the_synthesized_package_node_is_marked_as_virtual(db: str) -> None:
    """The row for the package itself is built for the answer, not read from the graph."""
    nodes, _ = _structure(db, "app.api")
    package = next(node for node in nodes if node.id == "app.api")

    assert package.type is NodeType.MODULE
    assert package.file_path == VIRTUAL_FILE_PATH


def test_depth_two_also_lists_what_each_module_holds(db: str) -> None:
    """One more hop is the module's own members, as for any other structural query."""
    nodes, _ = _structure(db, "app.api", depth=2)

    assert "app.api.routes.get_user" in {node.id for node in nodes}


def test_a_module_keeps_its_ordinary_answer(db: str) -> None:
    """`app.store` has symbols under it but no files: it is a module, not a package.

    Without the FILE-node discriminator this hijacks every module query and
    answers with a synthesized package node instead of the module's members.
    """
    nodes, edges = _structure(db, "app.store", depth=2)

    assert {node.id for node in nodes} == {"app.store", "app.store.save", "app.store.Store"}
    assert all(edge.type is EdgeType.CONTAINS for edge in edges)
    store_node = next(node for node in nodes if node.id == "app.store")
    assert store_node.type is NodeType.FILE
    assert store_node.file_path != VIRTUAL_FILE_PATH


def test_an_unknown_prefix_still_returns_nothing(db: str) -> None:
    """A typo must not become an empty package listing (#467 is the same failure)."""
    nodes, edges = _structure(db, "app.nope")

    assert nodes == []
    assert edges == []
