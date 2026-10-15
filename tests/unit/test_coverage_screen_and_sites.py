"""The coverage line applies the answer's own screen and says where each gap is (#641).

Exp3b (#640): impact on `calculate_total_price` reported `10 of 53 calls
unresolved`, but 9 of the 10 unresolved calls, and 27 of the 43 resolved ones,
came from tests the tree above it had left out. The one production gap,
`self.catalog_service.build_cards` in `FavoritesService._hydrate`, was shown as a
bare name, fourth of five, and no agent followed it.
"""

from collections.abc import Generator

import pytest

from cgis.api.mcp_server import _coverage_text
from cgis.core.coverage import TraversalCoverage, UnresolvedSite, unresolved_sites
from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.engine import QueryEngine, agent_screen
from cgis.storage.sqlite_store import SQLiteStore


@pytest.fixture
def store() -> Generator[SQLiteStore, None, None]:
    """An empty in-memory graph for each case."""
    s = SQLiteStore(":memory:")
    s.connect()
    yield s
    s.disconnect()


def _fn(node_id: str, file_path: str = "app/svc.py") -> Node:
    return Node(
        id=node_id,
        type=NodeType.FUNCTION,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=2,
    )


def _unknown(node_id: str) -> Node:
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


def _call(source: str, target: str, file_path: str, line: int, confidence: float = 1.0) -> Edge:
    return Edge(
        id=f"{source}->{target}",
        source=source,
        target=target,
        type=EdgeType.CALLS,
        confidence=confidence,
        file_path=file_path,
        line_number=line,
    )


def _graph(store: SQLiteStore) -> None:
    """`build_cards`: one production caller, one test caller, one production and one test gap."""
    store.save_graph(
        [
            _fn("catalog.build_cards"),
            _fn("catalog.search", "app/catalog.py"),
            _fn("tests.test_catalog.test_build", "tests/test_catalog.py"),
            _fn("favorites.hydrate", "app/favorites.py"),
            _fn("tests.test_fav.test_hydrate", "tests/test_fav.py"),
            _unknown("self.catalog_service.build_cards"),
            _unknown("service.build_cards"),
        ],
        [
            _call("catalog.search", "catalog.build_cards", "app/catalog.py", 10),
            _call(
                "tests.test_catalog.test_build", "catalog.build_cards", "tests/test_catalog.py", 5
            ),
            _call(
                "favorites.hydrate",
                "self.catalog_service.build_cards",
                "app/favorites.py",
                383,
                0.8,
            ),
            _call(
                "tests.test_fav.test_hydrate", "service.build_cards", "tests/test_fav.py", 7, 0.8
            ),
        ],
    )


def _impact(store: SQLiteStore, include_tests: bool) -> TraversalCoverage | None:
    """Impact coverage on `catalog.build_cards` under the agent screen."""
    screen = agent_screen(store, "catalog.build_cards", include_tests=include_tests)
    engine = QueryEngine(store)
    return engine.get_impact_result("catalog.build_cards", max_depth=3, screen=screen).coverage


def test_test_callers_leave_both_halves_of_the_count(store: SQLiteStore) -> None:
    _graph(store)
    coverage = _impact(store, include_tests=False)
    assert coverage is not None
    assert (coverage.calls_unresolved, coverage.calls_examined) == (1, 2)
    assert coverage.top_unresolved == [("self.catalog_service.build_cards", 1)]


def test_with_tests_included_everything_is_counted(store: SQLiteStore) -> None:
    _graph(store)
    coverage = _impact(store, include_tests=True)
    assert coverage is not None
    assert (coverage.calls_unresolved, coverage.calls_examined) == (2, 4)


def test_an_unresolved_call_says_where_it_is_made(store: SQLiteStore) -> None:
    _graph(store)
    coverage = _impact(store, include_tests=False)
    assert coverage is not None
    [site] = coverage.sites
    assert (site.target, site.caller, site.file_path, site.line) == (
        "self.catalog_service.build_cards",
        "favorites.hydrate",
        "app/favorites.py",
        383,
    )


def test_the_text_line_carries_the_place(store: SQLiteStore) -> None:
    _graph(store)
    coverage = _impact(store, include_tests=False)
    assert coverage is not None
    line = _coverage_text(coverage)
    assert line.startswith("coverage: 1 of 2 calls unresolved")
    assert "self.catalog_service.build_cards (1) at favorites.hydrate app/favorites.py:383" in line


def test_sites_are_capped_per_name(store: SQLiteStore) -> None:
    callers = [_fn(f"app.c{i}", "app/c.py") for i in range(6)]
    store.save_graph(
        [_fn("catalog.build_cards"), _unknown("svc.build_cards"), *callers],
        [_call(c.id, "svc.build_cards", "app/c.py", i, 0.8) for i, c in enumerate(callers)],
    )
    coverage = QueryEngine(store).get_impact_result("catalog.build_cards").coverage
    assert coverage is not None
    assert coverage.top_unresolved == [("svc.build_cards", 6)]
    assert len(coverage.sites) == 3


def test_flow_coverage_also_says_where(store: SQLiteStore) -> None:
    store.save_graph(
        [
            _fn("favorites.hydrate", "app/favorites.py"),
            _unknown("self.catalog_service.build_cards"),
        ],
        [
            _call(
                "favorites.hydrate",
                "self.catalog_service.build_cards",
                "app/favorites.py",
                383,
                0.8,
            )
        ],
    )
    coverage = QueryEngine(store).get_flow_result("favorites.hydrate").coverage
    assert coverage is not None
    assert [(s.caller, s.line) for s in coverage.sites] == [("favorites.hydrate", 383)]


def test_sites_follow_top_order_and_prefer_a_place() -> None:
    """Across names: `top`'s order. Within one: sites with a line first, then caller, line."""
    calls = [
        ("a.x", "z.caller", "z.py", 9),
        ("a.x", "b.caller", None, None),  # a decorator call: no place
        ("a.x", "b.caller", "b.py", 4),
        ("b.y", "c.caller", "c.py", 1),
    ]
    top = [("b.y", 1), ("a.x", 3)]
    sites = unresolved_sites(calls, top)
    assert [(s.target, s.caller, s.line) for s in sites] == [
        ("b.y", "c.caller", 1),
        ("a.x", "b.caller", 4),
        ("a.x", "z.caller", 9),
        ("a.x", "b.caller", None),
    ]


def test_the_text_line_shortens_the_caller_and_keeps_a_lineless_file() -> None:
    coverage = TraversalCoverage(
        basis="unresolved_calls_by_name",
        calls_examined=2,
        calls_unresolved=1,
        top_unresolved=[("router.get", 1)],
        sites=[
            UnresolvedSite(target="router.get", caller="pkg.mod.Klass.method", file_path="m.py")
        ],
    )
    assert "router.get (1) at Klass.method m.py;" in _coverage_text(coverage) + ";"
