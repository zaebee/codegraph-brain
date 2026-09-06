"""Unit tests for the orphan-class query (#415, spec PR3b).

The two filters this query lives or dies on each get a test that fails when the
filter stops being applied — those are the first two below, and they are the
pair #415 identifies as decisive: without the test filter all six of its worked
rows read as live, and without the re-export exclusion nothing is ever reported.
"""

import sqlite3
from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.context.orphans import find_orphan_classes
from cgis.storage.sqlite_store import SQLiteStore


def _node(
    node_id: str,
    node_type: NodeType = NodeType.CLASS,
    file_path: str = "app/adapters.py",
    namespace: NodeNamespace = NodeNamespace.INTERNAL,
) -> Node:
    """A node whose `is_test` is derived from its path, as at ingest."""
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=2,
        namespace=namespace,
    )


def _edge(source: str, target: str, edge_type: EdgeType) -> Edge:
    return Edge(
        id=f"{source}->{target}:{edge_type.value}", source=source, target=target, type=edge_type
    )


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    """An empty store the tests fill themselves."""
    with SQLiteStore(str(tmp_path / "g.db")) as opened:
        yield opened


def _save(store: SQLiteStore, nodes: list[Node], edges: list[Edge]) -> None:
    store.save_graph(nodes, edges)


def test_a_class_built_only_by_a_test_is_an_orphan(store: SQLiteStore) -> None:
    """The decisive filter: a test constructing a class is not a user of it.

    Both of #415's real orphans had three test constructions each. Count them
    and the query reports nothing.
    """
    _save(
        store,
        [
            _node("app.adapters.ReservationAdapter"),
            _node("tests.test_adapters", NodeType.FILE, "tests/test_adapters.py"),
        ],
        [_edge("tests.test_adapters", "app.adapters.ReservationAdapter", EdgeType.CALLS)],
    )
    report = find_orphan_classes(store)
    assert [o.fqn for o in report.orphans] == ["app.adapters.ReservationAdapter"]
    assert report.test_sources == 1


def test_a_re_exported_class_is_still_an_orphan(store: SQLiteStore) -> None:
    """`from .adapters import X` in a package __init__ is not a use.

    Every row of #415's table has one, orphans included, so counting
    IMPORTS_SYMBOL makes the query incapable of reporting anything.
    """
    _save(
        store,
        [
            _node("app.adapters.ReservationAdapter"),
            _node("app", NodeType.FILE, "app/__init__.py"),
        ],
        [_edge("app", "app.adapters.ReservationAdapter", EdgeType.IMPORTS_SYMBOL)],
    )
    assert [o.fqn for o in find_orphan_classes(store).orphans] == [
        "app.adapters.ReservationAdapter"
    ]


def test_a_constructed_class_is_not_an_orphan(store: SQLiteStore) -> None:
    """The ordinary live case."""
    _save(
        store,
        [_node("app.adapters.Live"), _node("app.svc.run", NodeType.FUNCTION, "app/svc.py")],
        [_edge("app.svc.run", "app.adapters.Live", EdgeType.CALLS)],
    )
    assert find_orphan_classes(store).orphans == []


def test_a_class_only_annotated_against_is_not_an_orphan(store: SQLiteStore) -> None:
    """An abstract port is never constructed; the reference edge is its only evidence.

    `CurrencyPort` in #415's table is live because three domains type against
    it. Drop REFERENCES and every Protocol nobody implements reads as dead.
    """
    _save(
        store,
        [
            _node("app.ports.CurrencyPort"),
            _node("app.svc.convert", NodeType.FUNCTION, "app/svc.py"),
        ],
        [_edge("app.svc.convert", "app.ports.CurrencyPort", EdgeType.REFERENCES)],
    )
    assert find_orphan_classes(store).orphans == []


def test_an_extended_class_is_not_an_orphan(store: SQLiteStore) -> None:
    """A base class with an implementation is used, even with no construction."""
    _save(
        store,
        [_node("app.ports.CurrencyPort"), _node("app.adapters.CurrencyAdapter")],
        [_edge("app.adapters.CurrencyAdapter", "app.ports.CurrencyPort", EdgeType.EXTENDS)],
    )
    assert [o.fqn for o in find_orphan_classes(store).orphans] == ["app.adapters.CurrencyAdapter"]


def test_a_class_containing_itself_is_not_rescued_by_containment(store: SQLiteStore) -> None:
    """CONTAINS is structure, not use — a module holding a class is not its user."""
    _save(
        store,
        [_node("app.adapters.Dead"), _node("app.adapters", NodeType.FILE)],
        [_edge("app.adapters", "app.adapters.Dead", EdgeType.CONTAINS)],
    )
    assert [o.fqn for o in find_orphan_classes(store).orphans] == ["app.adapters.Dead"]


def test_an_external_class_is_never_reported(store: SQLiteStore) -> None:
    """Only this repository's own code can be dead code in it."""
    _save(
        store,
        [_node("pydantic.BaseModel", namespace=NodeNamespace.EXTERNAL, file_path="EXTERNAL")],
        [],
    )
    assert find_orphan_classes(store).orphans == []


def test_a_test_class_is_never_reported(store: SQLiteStore) -> None:
    """A test's own helper class is not production dead code."""
    _save(store, [_node("tests.helpers.FakeClient", file_path="tests/helpers.py")], [])
    report = find_orphan_classes(store)
    assert report.orphans == []
    assert report.considered == 0


def test_prefix_narrows_on_a_dot_boundary(store: SQLiteStore) -> None:
    """`app.crud` must not pick up `app.crudX` — the trap #415's own sweep names."""
    _save(
        store,
        [
            _node("app.crud.Dead", file_path="app/crud.py"),
            _node("app.crudX.AlsoDead", file_path="app/crudX.py"),
            _node("app.other.Dead", file_path="app/other.py"),
        ],
        [],
    )
    report = find_orphan_classes(store, prefix="app.crud")
    assert [o.fqn for o in report.orphans] == ["app.crud.Dead"]
    assert report.considered == 1


def test_include_tests_counts_a_test_as_a_user(store: SQLiteStore) -> None:
    """The opt-in that turns the report into "unreachable from anywhere"."""
    _save(
        store,
        [
            _node("app.adapters.ReservationAdapter"),
            _node("tests.test_adapters", NodeType.FILE, "tests/test_adapters.py"),
        ],
        [_edge("tests.test_adapters", "app.adapters.ReservationAdapter", EdgeType.CALLS)],
    )
    assert find_orphan_classes(store, include_tests=True).orphans == []


def test_considered_counts_the_population_not_the_findings(store: SQLiteStore) -> None:
    """3 of 40 reads differently from 3 of 1800, so the denominator is reported."""
    _save(
        store,
        [_node("app.a.Live"), _node("app.b.Dead"), _node("app.svc.run", NodeType.FUNCTION)],
        [_edge("app.svc.run", "app.a.Live", EdgeType.CALLS)],
    )
    report = find_orphan_classes(store)
    assert [o.fqn for o in report.orphans] == ["app.b.Dead"]
    assert report.considered == 2


def test_a_pre_column_graph_is_backfilled_on_open(tmp_path: Path) -> None:
    """A graph ingested before `is_test` existed must still answer correctly.

    Without the backfill the migration leaves every row at 0, every test counts
    as production, and the query silently under-reports — the failure mode the
    column exists to prevent, reintroduced by the upgrade path. `is_test_path`
    is pure and `file_path` is already stored, so nothing needs re-ingesting.
    """
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, type TEXT, name TEXT, file_path TEXT,
            start_line INTEGER, end_line INTEGER, language TEXT,
            ontology_class TEXT, domains TEXT, confidence_score REAL,
            metadata TEXT, namespace TEXT NOT NULL DEFAULT 'INTERNAL'
        );
        CREATE TABLE edges (
            id TEXT PRIMARY KEY, source TEXT, target TEXT, type TEXT,
            weight REAL, confidence REAL, context TEXT, file_path TEXT,
            line_number INTEGER
        );
        INSERT INTO nodes VALUES
            ('app.a.Dead','CLASS','Dead','app/a.py',1,2,'python',NULL,'[]',1.0,'{}','INTERNAL'),
            ('tests.t','FILE','t','tests/test_a.py',1,2,'python',NULL,'[]',1.0,'{}','INTERNAL');
        INSERT INTO edges VALUES
            ('e1','tests.t','app.a.Dead','CALLS',1.0,1.0,NULL,'tests/test_a.py',1);
        """
    )
    conn.commit()
    conn.close()

    with SQLiteStore(db) as opened:
        report = find_orphan_classes(opened)
    assert report.test_sources == 1, "the migration did not backfill is_test from file_path"
    assert [o.fqn for o in report.orphans] == ["app.a.Dead"]
