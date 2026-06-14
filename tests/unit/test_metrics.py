"""Unit tests for the DuckDB analytical layer (#16)."""

from pathlib import Path

import pytest

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.render import metrics as metrics_module
from cgis.query.render.metrics import DuckDBAnalyzer
from cgis.storage.sqlite_store import SQLiteStore


def _node(node_id: str, node_type: NodeType = NodeType.FUNCTION) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path="m.py",
        start_line=1,
        end_line=2,
    )


def _write_db(tmp_path: Path, nodes: list[Node], edges: list[Edge]) -> str:
    db = str(tmp_path / "graph.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def test_zero_copy_attach_reads_sqlite_graph(tmp_path: Path) -> None:
    """DuckDBAnalyzer attaches to a SQLite graph.db and reads it (issue Test 1)."""
    db = _write_db(tmp_path, [_node("a"), _node("b")], [])
    with DuckDBAnalyzer(db) as analyzer:
        report = analyzer.architecture_report()
    # two isolated functions → both present, zero coupling
    assert {m.node_id for m in report.bottlenecks} >= {"a", "b"}


def test_coupling_in_degree_counts_callers(tmp_path: Path) -> None:
    """A node called by 5 others reports in_degree=5 (issue Test 2)."""
    callers = [_node(f"c{i}") for i in range(5)]
    target = _node("hub")
    edges = [Edge(id=f"e{i}", source=f"c{i}", target="hub", type=EdgeType.CALLS) for i in range(5)]
    db = _write_db(tmp_path, [*callers, target], edges)

    with DuckDBAnalyzer(db) as analyzer:
        metrics = analyzer.get_coupling_metrics()

    hub = next(m for m in metrics if m.node_id == "hub")
    assert hub.in_degree == 5
    assert hub.out_degree == 0


def test_out_degree_counts_callees(tmp_path: Path) -> None:
    """A node calling 3 others reports out_degree=3."""
    callees = [_node(f"d{i}") for i in range(3)]
    caller = _node("orchestrator")
    edges = [
        Edge(id=f"o{i}", source="orchestrator", target=f"d{i}", type=EdgeType.CALLS)
        for i in range(3)
    ]
    db = _write_db(tmp_path, [*callees, caller], edges)

    with DuckDBAnalyzer(db) as analyzer:
        metrics = analyzer.get_coupling_metrics()

    orch = next(m for m in metrics if m.node_id == "orchestrator")
    assert orch.out_degree == 3


def test_god_class_detected_by_declares_count(tmp_path: Path) -> None:
    """A class declaring 10 methods tops the god-class list (issue Test 3)."""
    klass = _node("app.Big", NodeType.CLASS)
    methods = [_node(f"app.Big.m{i}", NodeType.METHOD) for i in range(10)]
    small = _node("app.Small", NodeType.CLASS)
    small_methods = [_node("app.Small.only", NodeType.METHOD)]
    edges = [
        Edge(id=f"d{i}", source="app.Big", target=f"app.Big.m{i}", type=EdgeType.DECLARES)
        for i in range(10)
    ]
    edges.append(Edge(id="ds", source="app.Small", target="app.Small.only", type=EdgeType.DECLARES))
    db = _write_db(tmp_path, [klass, small, *methods, *small_methods], edges)

    with DuckDBAnalyzer(db) as analyzer:
        gods = analyzer.get_god_classes()

    assert gods[0].node_id == "app.Big"
    assert gods[0].out_degree == 10


def test_coupling_excludes_external_nodes(tmp_path: Path) -> None:
    """Builtins/external (e.g. raw_call:print) don't pollute the bottleneck list."""
    f = _node("app.f")
    edges = [Edge(id="ext", source="app.f", target="raw_call:print", type=EdgeType.CALLS)]
    db = _write_db(tmp_path, [f], edges)

    with DuckDBAnalyzer(db) as analyzer:
        metrics = analyzer.get_coupling_metrics()

    assert all(not m.node_id.startswith("raw_call:") for m in metrics)


def test_missing_database_raises_file_not_found(tmp_path: Path) -> None:
    """A nonexistent db path is a clear FileNotFoundError, not a duckdb crash."""
    with pytest.raises(FileNotFoundError, match="ingest"):
        DuckDBAnalyzer(str(tmp_path / "nope.db"))


def test_missing_duckdb_degrades_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the optional duckdb extra, constructing the analyzer raises a clear error."""
    db = _write_db(tmp_path, [_node("a")], [])
    monkeypatch.setattr(metrics_module, "duckdb", None)
    with pytest.raises(RuntimeError, match="analytics"):
        DuckDBAnalyzer(db)


def test_coupling_excludes_virtual_self_dispatch_nodes(tmp_path: Path) -> None:
    """Resolver pseudo-nodes (INTERNAL namespace, virtual file e.g. self.x.y) are excluded."""
    real = _node("app.real")
    virtual = Node(
        id="self._conn.execute",
        type=NodeType.FUNCTION,
        name="execute",
        file_path=VIRTUAL_FILE_PATH,
        start_line=0,
        end_line=0,
        namespace=NodeNamespace.INTERNAL,
    )
    edges = [Edge(id="v", source="app.real", target="self._conn.execute", type=EdgeType.CALLS)]
    db = _write_db(tmp_path, [real, virtual], edges)

    with DuckDBAnalyzer(db) as analyzer:
        ids = {m.node_id for m in analyzer.get_coupling_metrics()}

    assert "self._conn.execute" not in ids
    assert "app.real" in ids


def test_out_degree_excludes_external_callees(tmp_path: Path) -> None:
    """Fan-out counts only INTERNAL callees — stdlib/raw_call targets don't inflate it."""
    caller = _node("app.caller")
    internal_callee = _node("app.helper")
    edges = [
        Edge(id="i", source="app.caller", target="app.helper", type=EdgeType.CALLS),
        Edge(id="x1", source="app.caller", target="raw_call:print", type=EdgeType.CALLS),
        Edge(id="x2", source="app.caller", target="raw_call:typer", type=EdgeType.CALLS),
    ]
    db = _write_db(tmp_path, [caller, internal_callee], edges)

    with DuckDBAnalyzer(db) as analyzer:
        metrics = analyzer.get_coupling_metrics()

    caller_m = next(m for m in metrics if m.node_id == "app.caller")
    assert caller_m.out_degree == 1  # only app.helper, not the 2 externals


def test_architecture_report_honors_god_limit(tmp_path: Path) -> None:
    """architecture_report caps God classes at god_limit (the --limit plumbing target)."""
    nodes: list[Node] = []
    edges: list[Edge] = []
    for c in range(6):
        nodes.append(_node(f"app.C{c}", NodeType.CLASS))
        nodes.append(_node(f"app.C{c}.m", NodeType.METHOD))
        edges.append(
            Edge(id=f"d{c}", source=f"app.C{c}", target=f"app.C{c}.m", type=EdgeType.DECLARES)
        )
    db = _write_db(tmp_path, nodes, edges)

    with DuckDBAnalyzer(db) as analyzer:
        report = analyzer.architecture_report(god_limit=2)

    assert len(report.god_classes) == 2


def test_pagerank_hub_ranks_above_spokes(tmp_path: Path) -> None:
    """A node everything points to gets the highest PageRank (#231)."""
    spokes = [_node(f"app.s{i}") for i in range(5)]
    hub = _node("app.hub")
    edges = [
        Edge(id=f"e{i}", source=f"app.s{i}", target="app.hub", type=EdgeType.CALLS)
        for i in range(5)
    ]
    db = _write_db(tmp_path, [*spokes, hub], edges)

    with DuckDBAnalyzer(db) as analyzer:
        ranked = analyzer.get_pagerank()

    assert ranked[0].node_id == "app.hub"
    assert ranked[0].page_rank > 0
    # hub strictly outranks every spoke
    spoke_ranks = [m.page_rank for m in ranked if m.node_id != "app.hub"]
    assert all(ranked[0].page_rank > r for r in spoke_ranks)


def test_pagerank_ranks_all_internal_nodes(tmp_path: Path) -> None:
    """Every INTERNAL function/method is ranked; results are sorted descending."""
    nodes = [_node(f"app.f{i}") for i in range(4)]
    edges = [
        Edge(id=f"c{i}", source=f"app.f{i}", target=f"app.f{i + 1}", type=EdgeType.CALLS)
        for i in range(3)
    ]
    db = _write_db(tmp_path, nodes, edges)

    with DuckDBAnalyzer(db) as analyzer:
        ranked = analyzer.get_pagerank()

    assert {m.node_id for m in ranked} == {f"app.f{i}" for i in range(4)}
    ranks = [m.page_rank for m in ranked]
    assert ranks == sorted(ranks, reverse=True)


def test_pagerank_excludes_external_and_virtual(tmp_path: Path) -> None:
    """PageRank ignores raw_call/external and resolver virtual pseudo-nodes."""
    f = _node("app.f")
    virtual = Node(
        id="self._x.run",
        type=NodeType.FUNCTION,
        name="run",
        file_path=VIRTUAL_FILE_PATH,
        start_line=0,
        end_line=0,
        namespace=NodeNamespace.INTERNAL,
    )
    edges = [
        Edge(id="x", source="app.f", target="raw_call:print", type=EdgeType.CALLS),
        Edge(id="v", source="app.f", target="self._x.run", type=EdgeType.CALLS),
    ]
    db = _write_db(tmp_path, [f, virtual], edges)

    with DuckDBAnalyzer(db) as analyzer:
        ids = {m.node_id for m in analyzer.get_pagerank()}

    assert ids == {"app.f"}


def test_pagerank_empty_graph_returns_empty(tmp_path: Path) -> None:
    """No internal nodes → empty ranking, no crash."""
    db = _write_db(tmp_path, [], [])
    with DuckDBAnalyzer(db) as analyzer:
        assert analyzer.get_pagerank() == []


def _exclude_fixture(tmp_path: Path) -> str:
    """Graph mixing prod nodes, top-level tests, nested tests, and a 'testservice'
    false-positive — used by the --exclude segment tests (#234)."""
    nodes = [
        _node("core.svc.handler"),
        _node("tests.utils.rnd"),
        _node("domains.resv.tests.test_routes"),
        _node("domains.testservice.run"),
        _node("core.models.Thing", NodeType.CLASS),
        _node("core.models.Thing.m", NodeType.METHOD),
        _node("tests.fakes.FakeThing", NodeType.CLASS),
        _node("tests.fakes.FakeThing.m", NodeType.METHOD),
    ]
    edges = [
        # test helpers call the prod handler (so they have coupling and would rank)
        Edge(id="e1", source="tests.utils.rnd", target="core.svc.handler", type=EdgeType.CALLS),
        Edge(
            id="e2",
            source="domains.resv.tests.test_routes",
            target="core.svc.handler",
            type=EdgeType.CALLS,
        ),
        Edge(
            id="e3",
            source="core.svc.handler",
            target="domains.testservice.run",
            type=EdgeType.CALLS,
        ),
        Edge(
            id="d1",
            source="core.models.Thing",
            target="core.models.Thing.m",
            type=EdgeType.DECLARES,
        ),
        Edge(
            id="d2",
            source="tests.fakes.FakeThing",
            target="tests.fakes.FakeThing.m",
            type=EdgeType.DECLARES,
        ),
    ]
    return _write_db(tmp_path, nodes, edges)


def test_exclude_segment_drops_tests_from_all_sections(tmp_path: Path) -> None:
    """`exclude=['tests']` removes top-level AND nested test FQNs from every
    section, while keeping a 'testservice' that only contains the substring (#234)."""
    db = _exclude_fixture(tmp_path)
    with DuckDBAnalyzer(db) as analyzer:
        report = analyzer.architecture_report(exclude=["tests"])

    ids = (
        {m.node_id for m in report.bottlenecks}
        | {m.node_id for m in report.god_classes}
        | {m.node_id for m in report.critical}
    )
    # no FQN carries a 'tests' dot-segment anywhere
    assert not any(".tests." in f".{i}." for i in ids)
    assert "tests.utils.rnd" not in ids
    assert "domains.resv.tests.test_routes" not in ids
    assert "tests.fakes.FakeThing" not in ids
    # substring-but-not-a-segment survivor stays, as does the real prod node
    assert "domains.testservice.run" in ids
    assert "core.svc.handler" in ids


def test_exclude_is_opt_in_default_keeps_tests(tmp_path: Path) -> None:
    """Without exclude, test nodes still appear — the flag changed behaviour (#234)."""
    db = _exclude_fixture(tmp_path)
    with DuckDBAnalyzer(db) as analyzer:
        report = analyzer.architecture_report()
    bottleneck_ids = {m.node_id for m in report.bottlenecks}
    god_ids = {m.node_id for m in report.god_classes}
    assert "tests.utils.rnd" in bottleneck_ids
    assert "tests.fakes.FakeThing" in god_ids


def test_exclude_segment_escapes_like_metacharacters(tmp_path: Path) -> None:
    """An underscore in a segment is matched literally, not as the LIKE wildcard (#234)."""
    nodes = [_node("a.b_c.fn"), _node("a.bxc.fn")]
    db = _write_db(tmp_path, nodes, [])
    with DuckDBAnalyzer(db) as analyzer:
        ids = {m.node_id for m in analyzer.get_coupling_metrics(exclude=["b_c"])}
    # 'b_c' must drop only the literal 'b_c' segment, never 'bxc'
    assert "a.b_c.fn" not in ids
    assert "a.bxc.fn" in ids


def test_exclude_multiple_segments_drops_any_match(tmp_path: Path) -> None:
    """Repeatable exclude is AND-composed: a node is dropped if it matches ANY
    given segment; an unrelated node survives (#234, #235 review)."""
    nodes = [_node("a.tests.x"), _node("b.vendor.y"), _node("c.core.z")]
    db = _write_db(tmp_path, nodes, [])
    with DuckDBAnalyzer(db) as analyzer:
        ids = {m.node_id for m in analyzer.get_coupling_metrics(exclude=["tests", "vendor"])}
    assert ids == {"c.core.z"}


def test_exclude_empty_or_whitespace_segment_is_noop(tmp_path: Path) -> None:
    """Empty / whitespace-only segments are skipped, never excluding everything
    (#235 review — gemini/zaebee robustness nit)."""
    nodes = [_node("a.core.x"), _node("b.svc.y")]
    db = _write_db(tmp_path, nodes, [])
    with DuckDBAnalyzer(db) as analyzer:
        ids = {m.node_id for m in analyzer.get_coupling_metrics(exclude=["", "  "])}
    assert ids == {"a.core.x", "b.svc.y"}


def test_pagerank_rows_carry_internal_degree(tmp_path: Path) -> None:
    """PageRank rows report in/out degree over the same internal CALLS graph, so a
    high-rank-but-zero-in-degree leaf is legible as a dangling artifact (#237)."""
    hub = _node("app.hub")
    callers = [_node(f"app.c{i}") for i in range(3)]
    edges = [
        Edge(id=f"e{i}", source=f"app.c{i}", target="app.hub", type=EdgeType.CALLS)
        for i in range(3)
    ]
    db = _write_db(tmp_path, [hub, *callers], edges)

    with DuckDBAnalyzer(db) as analyzer:
        ranked = {m.node_id: m for m in analyzer.get_pagerank()}

    # hub: reached by 3 internal callers, calls nothing internal
    assert ranked["app.hub"].in_degree == 3
    assert ranked["app.hub"].out_degree == 0
    # a caller: pure source — out 1, in 0 (so its rank is floor/dangling only)
    assert ranked["app.c0"].in_degree == 0
    assert ranked["app.c0"].out_degree == 1
