"""The starting map an agent reads before it knows a single FQN (#478).

Every other query tool needs a name. This one answers "what is in this graph and
where do I start?", so its job is to stay small and to hand back prefixes the
next call can actually use.
"""

from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.analysis.overview import build_overview
from cgis.storage.sqlite_store import SQLiteStore


def _node(
    node_id: str,
    node_type: NodeType = NodeType.FUNCTION,
    *,
    is_test: bool = False,
    namespace: NodeNamespace = NodeNamespace.INTERNAL,
) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path=f"{node_id.replace('.', '/')}.py",
        start_line=1,
        end_line=2,
        is_test=is_test,
        namespace=namespace,
    )


def _graph() -> tuple[list[Node], list[Edge]]:
    """Two production packages of different sizes, a test package, and one stdlib node."""
    nodes = [
        _node("app.api.get_user"),
        _node("app.api.post_user"),
        _node("app.api.Router", NodeType.CLASS),
        _node("app.store.save"),
        _node("tests.api.test_get_user", is_test=True),
        _node("os.getcwd", namespace=NodeNamespace.STDLIB),
        _node("app/api.py", NodeType.FILE),
    ]
    edges = [
        Edge(id="e1", source="app.api.get_user", target="app.store.save", type=EdgeType.CALLS),
        Edge(id="e2", source="app.api.get_user", target="os.getcwd", type=EdgeType.CALLS),
    ]
    return nodes, edges


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = str(tmp_path / "graph.db")
    with SQLiteStore(path) as store:
        store.save_graph(*_graph())
    return path


def _overview(db_path: str, **kwargs: object) -> dict:
    with SQLiteStore(db_path) as store:
        return build_overview(store, **kwargs)  # type: ignore[arg-type]


def test_overview_counts_the_graph(db: str) -> None:
    """Sizes first: how big is this, and how much of it resolved."""
    report = _overview(db)

    assert report["symbols"] == {"CLASS": 1, "FUNCTION": 4}
    assert report["files"] == 1
    assert report["edges"] == 2
    assert report["unresolved_ratio"] == 0.0


def test_overview_ranks_packages_by_size_and_splits_tests(db: str) -> None:
    """The map is what an agent reads to pick where to look next."""
    report = _overview(db)

    assert report["packages"] == [
        {"prefix": "app.api", "symbols": 3},
        {"prefix": "app.store", "symbols": 1},
    ]
    assert report["test_packages"] == [{"prefix": "tests.api", "symbols": 1}]


def test_overview_excludes_external_code(db: str) -> None:
    """A stdlib node is not a package of this repository."""
    prefixes = {row["prefix"] for row in _overview(db)["packages"]}

    assert not any(prefix.startswith("os") for prefix in prefixes)


def test_overview_depth_controls_how_far_the_prefix_goes(db: str) -> None:
    """Depth 1 is the top level; a prefix never swallows the symbol's own name."""
    report = _overview(db, depth=1)

    assert report["packages"] == [{"prefix": "app", "symbols": 4}]


def test_overview_caps_the_listing_and_says_so(db: str) -> None:
    """The point is to save context, so the map has a hard ceiling."""
    report = _overview(db, limit=1)

    assert report["packages"] == [{"prefix": "app.api", "symbols": 3}]
    assert report["packages_omitted"] == 1


def test_overview_omits_the_counter_when_nothing_was_cut(db: str) -> None:
    """`packages_omitted: 0` on every full listing would be noise."""
    assert "packages_omitted" not in _overview(db)


def test_overview_of_an_empty_graph_says_so_instead_of_dividing_by_zero(tmp_path: Path) -> None:
    """An ingest that found nothing is a real state, and the first thing to report."""
    path = str(tmp_path / "empty.db")
    with SQLiteStore(path) as store:
        store.save_graph([], [])

    report = _overview(path)

    assert report["symbols"] == {}
    assert report["packages"] == []
    assert report["unresolved_ratio"] == 0.0
