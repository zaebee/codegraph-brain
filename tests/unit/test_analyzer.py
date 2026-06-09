"""Unit tests for AnalyzerEngine: cycle detection, Zone of Pain, God Object."""

from collections.abc import Generator
from typing import Any

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.analyzer import AnalyzerEngine, _tarjan_scc
from cgis.query.anomaly import AnomalyType
from cgis.storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> Generator[SQLiteStore, None, None]:
    """In-memory SQLite store, connected and disconnected per test."""
    s = SQLiteStore(":memory:")
    s.connect()
    yield s
    s.disconnect()


def _file_node(fqn: str) -> Node:
    """Helper: create an internal FILE node."""
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=fqn.replace(".", "/") + ".py",
        start_line=1,
        end_line=1,
        namespace=NodeNamespace.INTERNAL,
    )


def _class_node(fqn: str, **kwargs: Any) -> Node:  # noqa: ANN401
    """Helper: create an internal CLASS node."""
    return Node(
        id=fqn,
        type=NodeType.CLASS,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=fqn.split(".", maxsplit=1)[0] + ".py",
        start_line=1,
        end_line=50,
        namespace=NodeNamespace.INTERNAL,
        **kwargs,
    )


def _method_node(fqn: str) -> Node:
    """Helper: create an internal METHOD node."""
    return Node(
        id=fqn,
        type=NodeType.METHOD,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=fqn.split(".", maxsplit=1)[0] + ".py",
        start_line=1,
        end_line=5,
        namespace=NodeNamespace.INTERNAL,
    )


def _imports(src: str, tgt: str) -> Edge:
    """Helper: IMPORTS edge between two FQNs."""
    return Edge(id=f"{src}->imp->{tgt}", source=src, target=tgt, type=EdgeType.IMPORTS)


def _calls(src: str, tgt: str) -> Edge:
    """Helper: CALLS edge between two FQNs."""
    return Edge(id=f"{src}->call->{tgt}", source=src, target=tgt, type=EdgeType.CALLS)


def _contains(parent: str, child: str) -> Edge:
    """Helper: CONTAINS edge from parent to child."""
    return Edge(id=f"{parent}->has->{child}", source=parent, target=child, type=EdgeType.CONTAINS)


# ---------------------------------------------------------------------------
# _tarjan_scc (unit tests — no DB)
# ---------------------------------------------------------------------------


def test_tarjan_simple_cycle() -> None:
    """A→B→A is detected as one 2-node SCC."""
    adj = {"A": ["B"], "B": ["A"]}
    sccs = _tarjan_scc(adj)
    cycles = [s for s in sccs if len(s) >= 2]
    assert len(cycles) == 1
    assert set(cycles[0]) == {"A", "B"}


def test_tarjan_three_node_cycle() -> None:
    """A→B→C→A is detected as one 3-node SCC."""
    adj = {"A": ["B"], "B": ["C"], "C": ["A"]}
    sccs = _tarjan_scc(adj)
    cycles = [s for s in sccs if len(s) >= 2]
    assert len(cycles) == 1
    assert set(cycles[0]) == {"A", "B", "C"}


def test_tarjan_no_cycle() -> None:
    """A→B→C (DAG) produces no SCCs with len > 1."""
    adj = {"A": ["B"], "B": ["C"], "C": []}
    sccs = _tarjan_scc(adj)
    assert all(len(s) == 1 for s in sccs)


def test_tarjan_two_separate_cycles() -> None:
    """Two independent cycles are each detected separately."""
    adj = {"A": ["B"], "B": ["A"], "C": ["D"], "D": ["C"]}
    sccs = _tarjan_scc(adj)
    cycles = [s for s in sccs if len(s) >= 2]
    assert len(cycles) == 2


def test_tarjan_self_loop() -> None:
    """A self-loop (A→A) is not reported as a multi-node SCC."""
    adj = {"A": ["A"]}
    sccs = _tarjan_scc(adj)
    cycles = [s for s in sccs if len(s) >= 2]
    assert len(cycles) == 0


def test_tarjan_empty_graph() -> None:
    """Empty adjacency list produces no SCCs."""
    assert _tarjan_scc({}) == []


# ---------------------------------------------------------------------------
# detect_cycles (integration with store)
# ---------------------------------------------------------------------------


def test_detect_cycles_two_node(store: SQLiteStore) -> None:
    """Two files that mutually import each other produce one CIRCULAR_DEPENDENCY anomaly."""
    nodes = [_file_node("pkg.a"), _file_node("pkg.b")]
    edges = [_imports("pkg.a", "pkg.b"), _imports("pkg.b", "pkg.a")]
    store.save_graph(nodes, edges)

    report = AnalyzerEngine(store).detect_cycles()

    assert len(report) == 1
    anomaly = report[0]
    assert anomaly.type == AnomalyType.CIRCULAR_DEPENDENCY
    assert set(anomaly.metrics["cycle_members"]) == {"pkg.a", "pkg.b"}  # type: ignore[arg-type]
    assert anomaly.severity_score == pytest.approx(0.25)


def test_detect_cycles_three_node(store: SQLiteStore) -> None:
    """A→B→C→A cycle is flagged with severity > two-node cycle."""
    nodes = [_file_node("a"), _file_node("b"), _file_node("c")]
    edges = [_imports("a", "b"), _imports("b", "c"), _imports("c", "a")]
    store.save_graph(nodes, edges)

    report = AnalyzerEngine(store).detect_cycles()

    assert len(report) == 1
    assert report[0].metrics["cycle_length"] == 3
    assert report[0].severity_score > 0.25


def test_detect_cycles_no_cycle(store: SQLiteStore) -> None:
    """Linear imports produce no anomalies."""
    nodes = [_file_node("x"), _file_node("y"), _file_node("z")]
    edges = [_imports("x", "y"), _imports("y", "z")]
    store.save_graph(nodes, edges)

    assert AnalyzerEngine(store).detect_cycles() == []


def test_detect_cycles_ignores_external(store: SQLiteStore) -> None:
    """Imports from external packages (non-INTERNAL nodes) are not flagged."""
    internal = _file_node("myapp.core")
    external = Node(
        id="requests",
        type=NodeType.MODULE,
        name="requests",
        file_path="EXTERNAL",
        start_line=0,
        end_line=0,
        namespace=NodeNamespace.EXTERNAL,
    )
    edges = [_imports("myapp.core", "requests"), _imports("requests", "myapp.core")]
    store.save_graph([internal, external], edges)

    assert AnalyzerEngine(store).detect_cycles() == []


def test_detect_cycles_ignores_unresolved_imports(store: SQLiteStore) -> None:
    """Unresolved (raw_import:) edges are skipped and never form a cycle."""
    nodes = [_file_node("a"), _file_node("b")]
    edges = [
        Edge(id="a->raw->B", source="a", target="raw_import:B", type=EdgeType.IMPORTS),
        _imports("b", "a"),
    ]
    store.save_graph(nodes, edges)

    assert AnalyzerEngine(store).detect_cycles() == []


def test_detect_cycles_empty_graph(store: SQLiteStore) -> None:
    """Empty graph produces no anomalies."""
    assert AnalyzerEngine(store).detect_cycles() == []


# ---------------------------------------------------------------------------
# detect_zone_of_pain
# ---------------------------------------------------------------------------


def _seed_zone_of_pain(store: SQLiteStore, caller_count: int = 8) -> str:
    """Seed a stable-concrete class called by many callers."""
    target = _class_node("app.UserService")
    methods = [_method_node(f"app.UserService.m{i}") for i in range(3)]
    callers = [_class_node(f"app.Controller{i}") for i in range(caller_count)]
    caller_methods = [_method_node(f"app.Controller{i}.handle") for i in range(caller_count)]

    nodes = [target, *methods, *callers, *caller_methods]
    edges: list[Edge] = [_contains("app.UserService", m.id) for m in methods]
    edges += [
        e
        for i, cm in enumerate(caller_methods)
        for e in (_contains(f"app.Controller{i}", cm.id), _calls(cm.id, methods[0].id))
    ]

    store.save_graph(nodes, edges)
    return "app.UserService"


def test_detect_zone_of_pain_flags_stable_concrete(store: SQLiteStore) -> None:
    """A heavily-depended-upon concrete class is flagged as ZONE_OF_PAIN."""
    fqn = _seed_zone_of_pain(store, caller_count=8)
    anomalies = AnalyzerEngine(store).detect_zone_of_pain()

    assert any(a.focal_fqn == fqn and a.type == AnomalyType.ZONE_OF_PAIN for a in anomalies)


def test_detect_zone_of_pain_abstract_class_not_flagged(store: SQLiteStore) -> None:
    """A class flagged is_abstract in metadata is not flagged even with high Ca."""
    target = _class_node("app.BaseService", metadata={"is_abstract": True})
    methods = [_method_node(f"app.BaseService.m{i}") for i in range(3)]
    callers = [_class_node(f"app.C{i}") for i in range(8)]
    caller_methods = [_method_node(f"app.C{i}.h") for i in range(8)]

    nodes = [target, *methods, *callers, *caller_methods]
    edges: list[Edge] = [
        *[_contains("app.BaseService", m.id) for m in methods],
        *[
            e
            for i, cm in enumerate(caller_methods)
            for e in (_contains(f"app.C{i}", cm.id), _calls(cm.id, methods[0].id))
        ],
    ]

    store.save_graph(nodes, edges)
    anomalies = AnalyzerEngine(store).detect_zone_of_pain()

    assert not any(a.focal_fqn == "app.BaseService" for a in anomalies)


def test_detect_zone_of_pain_low_coupling_not_flagged(store: SQLiteStore) -> None:
    """A class with only 1 caller is not in the Zone of Pain."""
    _seed_zone_of_pain(store, caller_count=1)
    anomalies = AnalyzerEngine(store).detect_zone_of_pain()

    assert not any(a.focal_fqn == "app.UserService" for a in anomalies)


# ---------------------------------------------------------------------------
# detect_god_objects
# ---------------------------------------------------------------------------


def _seed_god_object(
    store: SQLiteStore,
    method_count: int = 12,
    efferent_count: int = 6,
) -> str:
    """Seed a class with many methods calling many distinct collaborators."""
    god = _class_node("app.GodClass")
    methods = [_method_node(f"app.GodClass.m{i}") for i in range(method_count)]
    collaborators = [_class_node(f"app.Collab{i}") for i in range(efferent_count)]
    collab_methods = [_method_node(f"app.Collab{i}.op") for i in range(efferent_count)]

    nodes = [god, *methods, *collaborators, *collab_methods]
    edges: list[Edge] = [
        *[_contains("app.GodClass", m.id) for m in methods],
        *[_contains(f"app.Collab{i}", cm.id) for i, cm in enumerate(collab_methods)],
        *[_calls(m.id, collab_methods[i].id) for i, m in enumerate(methods[:efferent_count])],
    ]

    store.save_graph(nodes, edges)
    return "app.GodClass"


def test_detect_god_object_flagged(store: SQLiteStore) -> None:
    """Class with 12 methods and 6 efferent targets is a God Object."""
    fqn = _seed_god_object(store, method_count=12, efferent_count=6)
    anomalies = AnalyzerEngine(store).detect_god_objects()

    assert any(a.focal_fqn == fqn and a.type == AnomalyType.GOD_OBJECT for a in anomalies)


def test_detect_god_object_small_class_not_flagged(store: SQLiteStore) -> None:
    """Class with 3 methods is not a God Object regardless of coupling."""
    _seed_god_object(store, method_count=3, efferent_count=6)
    anomalies = AnalyzerEngine(store).detect_god_objects()

    assert anomalies == []


def test_detect_god_object_low_efferent_not_flagged(store: SQLiteStore) -> None:
    """Class with many methods but low efferent coupling is not flagged."""
    _seed_god_object(store, method_count=15, efferent_count=2)
    anomalies = AnalyzerEngine(store).detect_god_objects()

    assert anomalies == []


# ---------------------------------------------------------------------------
# run (full report)
# ---------------------------------------------------------------------------


def test_run_empty_graph(store: SQLiteStore) -> None:
    """Empty graph produces a zero-anomaly report."""
    report = AnalyzerEngine(store).run()

    assert report.total_anomalies == 0
    assert report.anomalies == ()


def test_run_returns_all_detectors(store: SQLiteStore) -> None:
    """run() aggregates anomalies from all three detectors."""
    # Seed a cycle
    store.save_graph(
        [_file_node("a"), _file_node("b")],
        [_imports("a", "b"), _imports("b", "a")],
    )
    report = AnalyzerEngine(store).run()

    assert report.total_anomalies >= 1
    types = {a.type for a in report.anomalies}
    assert AnomalyType.CIRCULAR_DEPENDENCY in types
