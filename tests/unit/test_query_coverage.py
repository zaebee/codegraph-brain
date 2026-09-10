"""Per-query coverage on flow and impact traversals (#201).

`cgis_validate` answers "how well is the whole graph resolved"; these tests pin
the local question an agent actually has after a traversal: "is *this* answer
complete, or did unresolved calls cut it short?"

An unresolved call is stored as an edge into a virtual node whose namespace is
UNKNOWN (the resolver never leaves ``raw_call:`` in a saved graph). The two
directions see that differently, so each has its own basis:

* flow — the unresolved calls the expanded nodes *make*: dead ends downstream.
* impact — unresolved calls whose called name matches an expanded node: callers
  that may be missing upstream. An upper bound, since a name is not an identity.
"""

from collections.abc import Generator

import pytest

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.engine import BEHAVIORAL_EDGE_TYPES, QueryEngine
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore


@pytest.fixture
def store() -> Generator[SQLiteStore, None, None]:
    """An empty in-memory graph for each case."""
    s = SQLiteStore(":memory:")
    s.connect()
    yield s
    s.disconnect()


def _fn(node_id: str, name: str | None = None) -> Node:
    return Node(
        id=node_id,
        type=NodeType.FUNCTION,
        name=name or node_id.rsplit(".", maxsplit=1)[-1],
        file_path="f.py",
        start_line=1,
        end_line=2,
    )


def _unknown(node_id: str) -> Node:
    """A virtual node, shaped exactly as `ResolverEngine._make_virtual_node` makes one."""
    return Node(
        id=node_id,
        type=NodeType.FUNCTION,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path=VIRTUAL_FILE_PATH,
        start_line=0,
        end_line=0,
        namespace=NodeNamespace.UNKNOWN,
        confidence_score=0.8,
    )


def _call(source: str, target: str, confidence: float = 1.0) -> Edge:
    return Edge(
        id=f"{source}->{target}",
        source=source,
        target=target,
        type=EdgeType.CALLS,
        confidence=confidence,
    )


# --- flow -------------------------------------------------------------------


def test_flow_coverage_counts_calls_into_unknown(store: SQLiteStore) -> None:
    """One resolved call and one unresolved: half of what `caller` does is invisible."""
    store.save_graph(
        [_fn("caller"), _fn("solid"), _unknown("logger.warning")],
        [_call("caller", "solid"), _call("caller", "logger.warning", 0.8)],
    )

    result = QueryEngine(store).get_flow_result("caller", max_depth=3)

    assert result.coverage.basis == "unresolved_calls_made"
    assert result.coverage.calls_examined == 2
    assert result.coverage.calls_unresolved == 1
    assert result.coverage.unresolved_ratio == pytest.approx(0.5)
    assert result.coverage.top_unresolved == [("logger.warning", 1)]


def test_flow_coverage_names_what_is_unresolved_most_frequent_first(store: SQLiteStore) -> None:
    """A ratio cannot tell `logger.info` from `billing.charge`; the names can.

    Measured on cgis's own `IngestionPipeline.run`: 92 of 235 calls unresolved,
    and the top names were `get`, `model_copy`, `info`, `append` — receiver
    methods on untyped locals, none of them a first-party call the flow misses.
    """
    store.save_graph(
        [_fn("root"), _fn("b"), _fn("c"), _unknown("d.get"), _unknown("e.info")],
        [
            _call("root", "b"),
            _call("root", "c"),
            _call("root", "e.info", 0.8),
            _call("b", "d.get", 0.8),
            _call("c", "d.get", 0.8),
        ],
    )

    result = QueryEngine(store).get_flow_result("root", max_depth=3)

    assert result.coverage.top_unresolved == [("d.get", 2), ("e.info", 1)]


def test_flow_coverage_lists_at_most_ten_names(store: SQLiteStore) -> None:
    """The count covers every unresolved call; the list only the ten most frequent."""
    ghosts = [f"g{i:02}.run" for i in range(12)]
    store.save_graph(
        [_fn("root"), *(_unknown(g) for g in ghosts)],
        [_call("root", g, 0.8) for g in ghosts],
    )

    result = QueryEngine(store).get_flow_result("root", max_depth=3)

    assert result.coverage.calls_unresolved == 12
    assert [name for name, _ in result.coverage.top_unresolved] == ghosts[:10]


def test_flow_coverage_survives_a_confidence_floor(store: SQLiteStore) -> None:
    """`min_confidence` hides the unresolved edge from the result, not from the count.

    UNKNOWN calls sit at 0.8. A floor of 0.9 drops them inside the BFS, so a
    count taken from the rendered edges would read 0 — "trust this answer" —
    at exactly the moment the answer is least complete.
    """
    store.save_graph(
        [_fn("caller"), _fn("solid"), _unknown("ghost")],
        [_call("caller", "solid"), _call("caller", "ghost", 0.8)],
    )

    result = QueryEngine(store).get_flow_result("caller", max_depth=3, min_confidence=0.9)

    assert {e.target for e in result.edges} == {"solid"}
    assert result.coverage.calls_unresolved == 1
    assert result.coverage.calls_examined == 2


def test_flow_coverage_survives_external_pruning(store: SQLiteStore) -> None:
    """`show_external=False` prunes the UNKNOWN node; the count must not go with it."""
    store.save_graph(
        [_fn("caller"), _fn("solid"), _unknown("ghost")],
        [_call("caller", "solid"), _call("caller", "ghost", 0.8)],
    )

    result = QueryEngine(store).get_flow_result("caller", max_depth=3, show_external=False)

    assert "ghost" not in {n.id for n in result.nodes}
    assert result.coverage.calls_unresolved == 1


def test_flow_coverage_stops_at_the_depth_horizon(store: SQLiteStore) -> None:
    """An unresolved call beyond `max_depth` is truncation by depth, not a resolution gap."""
    store.save_graph(
        [_fn("a"), _fn("b"), _unknown("ghost")],
        [_call("a", "b"), _call("b", "ghost", 0.8)],
    )

    result = QueryEngine(store).get_flow_result("a", max_depth=1)

    assert result.coverage.calls_examined == 1
    assert result.coverage.calls_unresolved == 0


def test_flow_coverage_ignores_structural_edges(store: SQLiteStore) -> None:
    """A class CONTAINS its methods; those edges are not calls and must not dilute the ratio."""
    store.save_graph(
        [_fn("Cls"), _fn("Cls.m"), _unknown("ghost")],
        [
            Edge(id="Cls->Cls.m", source="Cls", target="Cls.m", type=EdgeType.CONTAINS),
            _call("Cls.m", "ghost", 0.8),
        ],
    )

    result = QueryEngine(store).get_flow_result("Cls", max_depth=3)

    assert result.coverage.calls_examined == 1
    assert result.coverage.calls_unresolved == 1


def test_flow_coverage_treats_a_dangling_or_raw_target_as_unresolved(
    store: SQLiteStore,
) -> None:
    """Same definition as `get_edge_stats`: no node, or a `raw_call:` target, is unresolved."""
    store.save_graph(
        [_fn("caller")],
        [_call("caller", "nowhere", 0.8), _call("caller", f"{RAW_CALL_PREFIX}mystery", 0.1)],
    )

    result = QueryEngine(store).get_flow_result("caller", max_depth=3)

    assert result.coverage.calls_examined == 2
    assert result.coverage.calls_unresolved == 2


def test_flow_coverage_of_a_leaf_is_zero_not_a_division_error(store: SQLiteStore) -> None:
    """Nothing examined means nothing unresolved; the ratio is 0.0, not NaN."""
    store.save_graph([_fn("leaf")], [])

    result = QueryEngine(store).get_flow_result("leaf", max_depth=3)

    assert result.coverage.calls_examined == 0
    assert result.coverage.unresolved_ratio == 0.0


def test_flow_result_carries_the_same_subgraph_as_get_flow_graph(store: SQLiteStore) -> None:
    """The new entry point is the old traversal plus a count — never a different answer."""
    store.save_graph(
        [_fn("a"), _fn("b"), _unknown("ghost")],
        [_call("a", "b"), _call("a", "ghost", 0.8)],
    )
    engine = QueryEngine(store)

    nodes, edges = engine.get_flow_graph("a", max_depth=3, allowed_edge_types=BEHAVIORAL_EDGE_TYPES)
    result = engine.get_flow_result("a", max_depth=3, allowed_edge_types=BEHAVIORAL_EDGE_TYPES)

    assert {n.id for n in result.nodes} == {n.id for n in nodes}
    assert {e.id for e in result.edges} == {e.id for e in edges}


# --- impact -----------------------------------------------------------------


def test_impact_coverage_counts_name_matched_unresolved_calls(store: SQLiteStore) -> None:
    """`repo.get_user()` did not resolve; it may be a caller of `svc.get_user` we cannot see."""
    store.save_graph(
        [_fn("svc.get_user"), _fn("api.handler"), _fn("jobs.sync"), _unknown("repo.get_user")],
        [_call("api.handler", "svc.get_user"), _call("jobs.sync", "repo.get_user", 0.8)],
    )

    result = QueryEngine(store).get_impact_result("svc.get_user", max_depth=3)

    assert result.coverage.basis == "unresolved_calls_by_name"
    assert result.coverage.calls_examined == 2
    assert result.coverage.calls_unresolved == 1
    assert result.coverage.unresolved_ratio == pytest.approx(0.5)
    assert result.coverage.top_unresolved == [("repo.get_user", 1)]


def test_impact_coverage_ignores_unresolved_calls_to_other_names(store: SQLiteStore) -> None:
    """An unresolved `logger.warning()` says nothing about who calls `get_user`."""
    store.save_graph(
        [_fn("svc.get_user"), _fn("api.handler"), _unknown("logger.warning")],
        [_call("api.handler", "svc.get_user"), _call("api.handler", "logger.warning", 0.8)],
    )

    result = QueryEngine(store).get_impact_result("svc.get_user", max_depth=3)

    assert result.coverage.calls_examined == 1
    assert result.coverage.calls_unresolved == 0


def test_impact_coverage_looks_for_hidden_callers_of_every_expanded_node(
    store: SQLiteStore,
) -> None:
    """A missing caller of an intermediate caller is missing impact too."""
    store.save_graph(
        [_fn("svc.core"), _fn("svc.wrap"), _fn("cli.run"), _unknown("x.wrap")],
        [_call("svc.wrap", "svc.core"), _call("cli.run", "x.wrap", 0.8)],
    )

    result = QueryEngine(store).get_impact_result("svc.core", max_depth=3)

    assert result.coverage.calls_unresolved == 1


def test_impact_coverage_stops_at_the_depth_horizon(store: SQLiteStore) -> None:
    """A hidden caller of a node the BFS never expanded is beyond the answer's horizon."""
    store.save_graph(
        [_fn("svc.core"), _fn("svc.wrap"), _fn("cli.run"), _unknown("x.wrap")],
        [_call("svc.wrap", "svc.core"), _call("cli.run", "x.wrap", 0.8)],
    )

    result = QueryEngine(store).get_impact_result("svc.core", max_depth=1)

    assert result.coverage.calls_unresolved == 0
