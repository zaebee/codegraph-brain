"""DISPATCHES: a base-class method to each override of it (#545).

A call through a base-typed receiver resolves to the base method. Without these
edges, impact on an override never reaches the callers that go through the base.
"""

import sqlite3
from pathlib import Path

import pytest

from cgis.api.mcp_server import cgis_ingest
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.engine import QueryEngine, agent_screen
from cgis.resolver.symbols import DISPATCH_CONFIDENCE
from cgis.storage.sqlite_store import SQLiteStore

_SOURCES = {
    "shapes.py": """
class Base:
    def __init__(self):
        pass

    def area(self):
        return 0

    def name(self):
        return "base"


class Square(Base):
    def __init__(self):
        super().__init__()

    def area(self):
        return 4


class Middle(Base):
    pass


class Leaf(Middle):
    def area(self):
        return 1


class Tuned(Square):
    def area(self):
        return 9


class Left(Base):
    def name(self):
        return "left"


class Right(Base):
    pass


class Diamond(Left, Right):
    def name(self):
        return "diamond"
""",
    "use.py": """
from shapes import Base


def measure(shape: Base):
    return shape.area()


def report(shape: Base):
    return measure(shape)
""",
    "tests/fakes.py": """
from shapes import Base


class FakeShape(Base):
    def area(self):
        return -1
""",
}


@pytest.fixture
def db(tmp_path: Path) -> str:
    """The shapes repo, ingested."""
    for rel, source in _SOURCES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    db_path = str(tmp_path / "graph.db")
    cgis_ingest(str(tmp_path), db_path)
    return db_path


def _edges(db: str, edge_type: EdgeType) -> list[Edge]:
    """Every stored edge of one type."""
    with SQLiteStore(db) as store:
        return [e for e in store.get_all_edges() if e.type == edge_type]


def _dispatches(db: str) -> dict[tuple[str, str], Edge]:
    """Every DISPATCHES edge, keyed by (source, target)."""
    return {(e.source, e.target): e for e in _edges(db, EdgeType.DISPATCHES)}


def test_an_override_is_reached_from_the_base_method(db: str) -> None:
    edge = _dispatches(db)[("shapes.Base.area", "shapes.Square.area")]

    assert edge.confidence == DISPATCH_CONFIDENCE
    assert edge.file_path == "shapes.py"


def test_impact_on_an_override_reaches_a_caller_through_the_base(db: str) -> None:
    """The issue: `measure(shape: Base)` calls `Base.area`; `Square.area` may run."""
    with SQLiteStore(db) as store:
        nodes, _ = QueryEngine(store).get_impact_graph("shapes.Square.area", max_depth=3)

    assert "use.measure" in {n.id for n in nodes}


def test_trace_from_the_caller_reaches_the_override(db: str) -> None:
    with SQLiteStore(db) as store:
        nodes, _ = QueryEngine(store).get_flow_graph("use.measure", max_depth=3)

    assert "shapes.Square.area" in {n.id for n in nodes}


def test_a_class_that_does_not_redefine_the_method_is_skipped_over(db: str) -> None:
    """`Middle` has no `area`; `Leaf.area` links to the nearest one up, `Base.area`."""
    assert ("shapes.Base.area", "shapes.Leaf.area") in _dispatches(db)


def test_a_chain_of_overrides_is_a_chain_of_edges(db: str) -> None:
    """`Tuned.area` overrides `Square.area`, not `Base.area` directly."""
    edges = _dispatches(db)

    assert ("shapes.Square.area", "shapes.Tuned.area") in edges
    assert ("shapes.Base.area", "shapes.Tuned.area") not in edges


def test_dunder_methods_get_no_edge(db: str) -> None:
    assert not any(src.endswith("__init__") for src, _ in _dispatches(db))


def test_an_override_in_test_code_gets_no_edge(db: str) -> None:
    """A fake is not where a production call dispatches to."""
    assert not any(dst.startswith("tests.") for _, dst in _dispatches(db))


def test_a_diamond_links_each_override_once_per_distinct_base(db: str) -> None:
    """`Diamond.name` reaches `Left.name` via Left and `Base.name` via Right."""
    to_diamond = {src for src, dst in _dispatches(db) if dst == "shapes.Diamond.name"}

    assert to_diamond == {"shapes.Left.name", "shapes.Base.name"}


def test_dispatch_edges_are_not_calls(db: str) -> None:
    """Coupling, drift and coverage count CALLS; a synthesized edge must not inflate them."""
    assert not any(e.target == "shapes.Square.area" for e in _edges(db, EdgeType.CALLS))


def test_an_override_added_later_is_linked_on_an_incremental_ingest(
    db: str, tmp_path: Path
) -> None:
    (tmp_path / "late.py").write_text(
        "from shapes import Base\n\nclass Late(Base):\n    def area(self):\n        return 2\n",
        encoding="utf-8",
    )
    cgis_ingest(str(tmp_path), db)

    assert ("shapes.Base.area", "late.Late.area") in _dispatches(db)


def test_resolution_stats_leave_dispatch_edges_out(db: str) -> None:
    """Synthesized edges are internal by construction; counting them would flatter the ratio."""
    with SQLiteStore(db) as store:
        stats = store.get_edge_stats()
        everything = len(store.get_all_edges())

    assert stats.total == everything - len(_dispatches(db))


_HIERARCHY = {
    "a.py": "class A:\n    def m(self):\n        return 1\n\n    def n(self):\n        return 1\n",
    "b.py": "from a import A\n\n\nclass B(A):\n    pass\n",
    "c.py": (
        "from a import A\nfrom b import B\n\n\n"
        "class C(B):\n    def m(self):\n        return 2\n\n\n"
        "class D(A):\n    def n(self):\n        return 2\n"
    ),
}


@pytest.fixture
def hierarchy(tmp_path: Path) -> tuple[Path, str]:
    """A → B (no m) → C.m, and A → D.n, across three files; ingested."""
    for rel, source in _HIERARCHY.items():
        (tmp_path / rel).write_text(source, encoding="utf-8")
    db_path = str(tmp_path / "graph.db")
    cgis_ingest(str(tmp_path), db_path)
    return tmp_path, db_path


_LINKS = {("a.A.m", "c.C.m"), ("a.A.n", "c.D.n")}


@pytest.mark.parametrize("edited", ["a.py", "c.py"])
def test_a_body_edit_keeps_every_dispatch_edge(hierarchy: tuple[Path, str], edited: str) -> None:
    """A body-only edit is an incremental run that rebuilds nothing (found in review).

    Editing the base file dropped both edges — their source is in it — and nothing
    put them back. Editing `c.py` lost `A.m → C.m`, because the walk past the
    unchanged middle class `B` needs `b.py`'s EXTENDS edge, which an incremental
    run does not load. The pass now runs over the stored graph after each write.
    """
    root, db = hierarchy
    assert set(_dispatches(db)) == _LINKS
    path = root / edited
    path.write_text(path.read_text(encoding="utf-8").replace("return", "return 0 +"))

    cgis_ingest(str(root), db)

    assert set(_dispatches(db)) == _LINKS


# --- a dispatch hop costs no depth (#645) ------------------------------------
#
# A caller of `Base.area` is a direct caller of whatever `area` runs. Counting the
# DISPATCHES hop as a level put every such caller one level deeper, so at depth 1
# impact showed none of them (raft: 57% of functions with a complete caller set,
# 77% with the hop free).


def test_impact_at_depth_one_shows_a_caller_through_the_base(db: str) -> None:
    with SQLiteStore(db) as store:
        nodes, _ = QueryEngine(store).get_impact_graph("shapes.Square.area", max_depth=1)

    assert "use.measure" in {n.id for n in nodes}


def test_impact_through_a_chain_of_overrides_costs_no_depth(db: str) -> None:
    """`Tuned.area` ← `Square.area` ← `Base.area` ← `measure`: still depth 1."""
    with SQLiteStore(db) as store:
        nodes, _ = QueryEngine(store).get_impact_graph("shapes.Tuned.area", max_depth=1)

    assert "use.measure" in {n.id for n in nodes}


def test_trace_at_depth_one_reaches_the_override(db: str) -> None:
    with SQLiteStore(db) as store:
        nodes, _ = QueryEngine(store).get_flow_graph("use.measure", max_depth=1)

    assert "shapes.Square.area" in {n.id for n in nodes}


def test_a_dispatch_hop_does_not_make_depth_free_for_plain_calls(db: str) -> None:
    """Only DISPATCHES is free: `report` calls `measure`, a level further out."""
    with SQLiteStore(db) as store:
        engine = QueryEngine(store)
        at_one = {n.id for n in engine.get_impact_graph("shapes.Square.area", max_depth=1)[0]}
        at_two = {n.id for n in engine.get_impact_graph("shapes.Square.area", max_depth=2)[0]}

    assert "use.report" not in at_one
    assert "use.report" in at_two


def test_impact_on_an_override_does_not_reach_its_siblings(db: str) -> None:
    """Up to `Base.area` and its callers; never back down to `Leaf.area`."""
    with SQLiteStore(db) as store:
        nodes, _ = QueryEngine(store).get_impact_graph("shapes.Square.area", max_depth=1)

    assert "shapes.Leaf.area" not in {n.id for n in nodes}


def test_trace_from_an_override_does_not_climb_to_its_base(db: str) -> None:
    with SQLiteStore(db) as store:
        nodes, _ = QueryEngine(store).get_flow_graph("shapes.Square.area", max_depth=1)

    ids = {n.id for n in nodes}
    assert "shapes.Base.area" not in ids
    assert "shapes.Leaf.area" not in ids


def test_impact_through_the_agent_screen_sees_the_caller_at_depth_one(db: str) -> None:
    """The MCP path: `agent_screen`, a node budget, coverage."""
    with SQLiteStore(db) as store:
        screen = agent_screen(store, "shapes.Square.area", include_tests=False)
        result = QueryEngine(store).get_impact_result(
            "shapes.Square.area", max_depth=1, screen=screen, max_nodes=50
        )

    assert "use.measure" in {n.id for n in result.nodes}


# --- the node budget with a dispatch fan-out (#645 review) -------------------


def _fan_out(store: SQLiteStore, overrides: int, base: str = "b.Base.m") -> None:
    """`base` dispatching to `overrides` subclasses, and a caller of the base."""

    def fn(node_id: str) -> Node:
        return Node(
            id=node_id,
            type=NodeType.METHOD,
            name=node_id.rsplit(".", maxsplit=1)[-1],
            file_path="b.py",
            start_line=1,
            end_line=2,
        )

    subs = [f"s{i:02d}.Sub.m" for i in range(overrides)]
    edges = [
        Edge(id=f"d{i}", source=base, target=s, type=EdgeType.DISPATCHES, confidence=0.5)
        for i, s in enumerate(subs)
    ]
    edges.append(Edge(id="c", source="a.caller", target=base, type=EdgeType.CALLS))
    store.save_graph([fn(base), fn("a.caller"), *map(fn, subs)], edges)


def test_a_dispatch_fan_out_from_the_root_respects_the_node_budget() -> None:
    with SQLiteStore(":memory:") as store:
        _fan_out(store, 10)
        result = QueryEngine(store).get_flow_result("b.Base.m", max_depth=1, max_nodes=3)

    assert len(result.nodes) <= 3
    assert result.truncation is not None
    assert result.truncation.depth == 0  # the root's own level was cut


def test_a_cut_leaves_no_dangling_dispatch_edge() -> None:
    """The base sorts after its overrides, so an id-ordered cut would keep an override alone."""
    with SQLiteStore(":memory:") as store:
        _fan_out(store, 10, base="z.Base.m")
        result = QueryEngine(store).get_flow_result("a.caller", max_depth=1, max_nodes=2)

    ids = {n.id for n in result.nodes}
    assert all(e.source in ids and e.target in ids for e in result.edges)


def test_reading_one_edge_type_uses_an_index(tmp_path: Path) -> None:
    """Every walk reads DISPATCHES whole: a scan of 95k edges for 49 rows on owner-api
    cost 33 ms a query, the index 0.24 ms (#645 measurement)."""
    db = str(tmp_path / "g.db")
    with SQLiteStore(db):
        pass  # creates the schema
    with sqlite3.connect(db) as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM edges WHERE type = ? ORDER BY rowid",
            (EdgeType.DISPATCHES.value,),
        ).fetchall()

    assert "idx_edges_type" in " ".join(str(tuple(row)) for row in plan)


def test_a_confidence_floor_above_dispatch_closes_nothing() -> None:
    with SQLiteStore(":memory:") as store:
        _fan_out(store, 3)
        nodes, _ = QueryEngine(store).get_flow_graph("b.Base.m", max_depth=1, min_confidence=0.6)

    assert {n.id for n in nodes} == {"b.Base.m"}
