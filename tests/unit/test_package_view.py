"""`get_structure` on a package prefix (#487).

A package is not a node: it exists in the graph only if it has an `__init__.py`,
and even then containment runs file → symbol, so the node holds nothing. Rather
than mint PACKAGE nodes into every graph, the structural query answers a prefix
by listing the modules under it.

The discriminator is FILE nodes *strictly under* the id. A module has symbols
under it but no files, so `pkg.mod` keeps its ordinary answer.
"""

import json
from pathlib import Path

import pytest

from cgis.api.mcp_server import cgis_get_structure
from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
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


def test_the_synthesized_package_row_carries_its_directory(db: str) -> None:
    """The row stands for a directory, so it names one.

    Not VIRTUAL_FILE_PATH: the Mermaid renderer styles an INTERNAL node with that
    path as *unresolved external*, which is the wrong claim about a package that
    exists on disk (#490 review).
    """
    nodes, _ = _structure(db, "app.api")
    package = next(node for node in nodes if node.id == "app.api")

    assert package.type is NodeType.MODULE
    assert package.file_path == "app/api"


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


def test_an_unknown_prefix_still_returns_nothing(db: str) -> None:
    """A typo must not become an empty package listing (#467 is the same failure)."""
    nodes, edges = _structure(db, "app.nope")

    assert nodes == []
    assert edges == []


def _module_with_children(tmp_path: Path) -> str:
    """`app.utils` is both a file and a directory — the `utils.ts` beside `utils/` shape."""
    nodes, edges = _graph()
    nodes += [
        _file("app.utils"),
        _symbol("app.utils.dedupe"),
        _file("app.utils.mapper"),
        _symbol("app.utils.mapper.to_view"),
    ]
    edges += [
        Edge(id="c4", source="app.utils", target="app.utils.dedupe", type=EdgeType.CONTAINS),
        Edge(
            id="c5",
            source="app.utils.mapper",
            target="app.utils.mapper.to_view",
            type=EdgeType.CONTAINS,
        ),
    ]
    path = str(tmp_path / "collide.db")
    with SQLiteStore(path) as store:
        store.save_graph(nodes, edges)
    return path


def test_a_file_that_is_also_a_package_keeps_its_own_members(tmp_path: Path) -> None:
    """`utils.ts` beside `utils/`, or an `__init__.py` holding code (#490 review).

    Replacing instead of merging lost 51 nodes of one real backend package and both
    functions of this repository's own `ui/src/utils.ts`.
    """
    db = _module_with_children(tmp_path)

    nodes, _edges = _structure(db, "app.utils", depth=2)
    ids = {node.id for node in nodes}

    assert "app.utils.dedupe" in ids, "the file's own member"
    assert "app.utils.mapper" in ids, "the sibling module"
    assert "app.utils.mapper.to_view" in ids


def test_a_real_node_stays_the_root_row(tmp_path: Path) -> None:
    """When the prefix is a file, the answer is rooted on that file, not a synthesized row."""
    db = _module_with_children(tmp_path)

    nodes, _ = _structure(db, "app.utils")
    root = next(node for node in nodes if node.id == "app.utils")

    assert root.type is NodeType.FILE
    assert root.file_path == "app/utils.py"


def test_only_direct_children_are_listed(tmp_path: Path) -> None:
    """A listing is one hop down; the whole subtree cost 106k tokens at depth 2 (#490 review)."""
    nodes, edges = _structure(db_with_nesting(tmp_path), "app", depth=1)
    ids = {node.id for node in nodes}

    assert ids == {"app", "app.api", "app.store"}
    assert {edge.target for edge in edges} == {"app.api", "app.store"}


def db_with_nesting(tmp_path: Path) -> str:
    """`app` holds the package `app.api` and the module `app.store`."""
    path = str(tmp_path / "nested.db")
    with SQLiteStore(path) as store:
        store.save_graph(*_graph())
    return path


def test_a_prefix_is_not_suffix_resolved_into_another_tree(tmp_path: Path) -> None:
    """Why the package check runs before FQN resolution (#490 review).

    `api.dependencies` is a package here and also a dot-boundary suffix of
    `app.api.dependencies` — the shape seen on a real backend. Resolving first
    answers about the other tree.
    """
    nodes = [
        _file("api.dependencies.clients"),
        _symbol("api.dependencies.clients.get_db"),
        _file("app.api.dependencies"),
        _symbol("app.api.dependencies.other"),
    ]
    edges = [
        Edge(
            id="c1",
            source="api.dependencies.clients",
            target="api.dependencies.clients.get_db",
            type=EdgeType.CONTAINS,
        ),
        Edge(
            id="c2",
            source="app.api.dependencies",
            target="app.api.dependencies.other",
            type=EdgeType.CONTAINS,
        ),
    ]
    db = str(tmp_path / "suffix.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)

    answer = cgis_get_structure("api.dependencies", db, depth=1, output_format="json")
    payload = json.loads(answer[answer.find("{") :])

    assert payload["root"] == "api.dependencies"
    assert {node["fqn"] for node in payload["nodes"]} == {
        "api.dependencies",
        "api.dependencies.clients",
    }
    assert "app.api.dependencies" not in answer


@pytest.mark.parametrize("prefix", ["app.nope", "app.%", "app._pi", "%"])
def test_a_typo_is_an_error_not_an_empty_listing(db: str, prefix: str) -> None:
    """LIKE wildcards are literal characters in an FQN, and an unknown id stays an error.

    Without escaping, `app.%` would match every module and answer as if the caller
    had named a real package (#490 review).
    """
    assert cgis_get_structure(prefix, db, output_format="json").startswith("❌")


def test_external_files_are_not_a_package(tmp_path: Path) -> None:
    """`files_under` is INTERNAL-only: a boundary node must not become a package."""
    nodes, edges = _graph()
    nodes.append(
        Node(
            id="vendor.lib.thing",
            type=NodeType.FILE,
            name="thing",
            file_path="vendor/lib/thing.py",
            start_line=1,
            end_line=1,
            namespace=NodeNamespace.EXTERNAL,
        )
    )
    db = str(tmp_path / "external.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)

    assert cgis_get_structure("vendor.lib", db, output_format="json").startswith("❌")
