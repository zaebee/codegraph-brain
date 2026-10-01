"""`cgis metrics` reports the unresolved-edge share beside its rankings (#451).

Before this the ratio was only in `cgis validate`, so a metrics reader saw a
module ranked "uncoupled" with no way to tell that most of its calls simply
point nowhere. The number must be `validate`'s own: one graph, one ratio.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cgis.cli import app
from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.render.metrics import DuckDBAnalyzer, resolution_metric
from cgis.storage.sqlite_store import SQLiteStore

SRC = Path(__file__).resolve().parents[2] / "src" / "cgis"
runner = CliRunner()


def _node(node_id: str, namespace: NodeNamespace = NodeNamespace.INTERNAL) -> Node:
    return Node(
        id=node_id,
        type=NodeType.FUNCTION,
        name=node_id.rsplit(".", 1)[-1],
        file_path="m.py",
        start_line=1,
        end_line=2,
        namespace=namespace,
    )


def _call(source: str, target: str) -> Edge:
    return Edge(id=f"{source}->{target}", source=source, target=target, type=EdgeType.CALLS)


@pytest.fixture
def mixed_db(tmp_path: Path) -> str:
    """Two domains; every unresolved shape `validate` counts, plus resolved ones.

    `a.*` emits 4 edges, 3 unresolved (an UNKNOWN node, a `raw_call:`, a target
    with no node); `b.*` emits 2, none unresolved (internal, stdlib).
    """
    nodes = [
        _node("a.f"),
        _node("a.g"),
        _node("b.h"),
        _node("tests.t"),
        _node("mystery.x", NodeNamespace.UNKNOWN),
        _node("json.dumps", NodeNamespace.STDLIB),
    ]
    edges = [
        _call("a.f", "a.g"),
        _call("a.f", "mystery.x"),
        _call("a.g", "raw_call:helper"),
        _call("a.g", "nowhere.y"),
        _call("b.h", "a.f"),
        _call("b.h", "json.dumps"),
        _call("tests.t", "raw_call:fixture"),
    ]
    db = str(tmp_path / "graph.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def test_unfiltered_it_is_validates_number(mixed_db: str) -> None:
    with SQLiteStore(mixed_db) as store:
        stats = store.get_edge_stats()
    with DuckDBAnalyzer(mixed_db) as analyzer:
        resolution = resolution_metric(analyzer)
    assert (resolution.total_edges, resolution.unresolved_edges) == (stats.total, stats.unresolved)
    assert resolution.unresolved_ratio == pytest.approx(stats.unresolved_ratio)
    assert (resolution.total_edges, resolution.unresolved_edges) == (7, 4)


def test_scope_counts_the_edges_its_code_emits(mixed_db: str) -> None:
    # By source: the unresolved edges have no target inside the scope at all,
    # so filtering by target would report a clean subtree.
    with DuckDBAnalyzer(mixed_db) as analyzer:
        a = resolution_metric(analyzer, scope=["a"])
        b = resolution_metric(analyzer, scope=["b"])
    assert (a.total_edges, a.unresolved_edges, a.unresolved_ratio) == (4, 3, 0.75)
    assert (b.total_edges, b.unresolved_edges, b.unresolved_ratio) == (2, 0, 0.0)


def test_exclude_drops_the_edges_of_excluded_code(mixed_db: str) -> None:
    with DuckDBAnalyzer(mixed_db) as analyzer:
        resolution = resolution_metric(analyzer, exclude=["tests"])
    assert (resolution.total_edges, resolution.unresolved_edges) == (6, 3)


def test_a_scope_with_no_edges_is_zero_not_a_division_error(mixed_db: str) -> None:
    with DuckDBAnalyzer(mixed_db) as analyzer:
        resolution = resolution_metric(analyzer, scope=["absent"])
    assert (resolution.total_edges, resolution.unresolved_ratio) == (0, 0.0)


def test_the_report_carries_it(mixed_db: str) -> None:
    with DuckDBAnalyzer(mixed_db) as analyzer:
        report = analyzer.architecture_report(scope=["a"])
    assert report.resolution.unresolved_edges == 3


def test_on_a_real_graph_it_matches_validate(tmp_path: Path) -> None:
    """The same number as `cgis validate` on cgis's own graph, not just on a fixture.

    Every unresolved shape the resolver really produces is in here — virtual
    UNKNOWN nodes, dangling targets — so a case the fixture missed shows up as
    a disagreement between the two counts.
    """
    db = str(tmp_path / "self.db")
    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(SRC), store=store)
        stats = store.get_edge_stats()
    with DuckDBAnalyzer(db) as analyzer:
        resolution = resolution_metric(analyzer)
    assert stats.total > 1000, "self-ingest produced too small a graph to mean anything"
    assert stats.unresolved > 0, "a real graph with nothing unresolved checks nothing here"
    assert (resolution.total_edges, resolution.unresolved_edges) == (stats.total, stats.unresolved)


def test_cli_text_and_json_report_it(mixed_db: str) -> None:
    text = runner.invoke(app, ["metrics", "--db", mixed_db])
    assert text.exit_code == 0, text.output
    assert "Unresolved edges: 4 of 7 (57.1%)" in text.stdout

    raw = runner.invoke(app, ["metrics", "--db", mixed_db, "--format", "json"])
    assert raw.exit_code == 0, raw.output
    assert json.loads(raw.stdout)["resolution"] == {
        "total_edges": 7,
        "unresolved_edges": 4,
        "unresolved_ratio": pytest.approx(4 / 7),
    }
