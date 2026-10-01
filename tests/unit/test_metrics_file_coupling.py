"""`cgis metrics` reports Martin's coupling per file (#451).

Node coupling ranks functions; a file is the unit a refactor moves. Ca and Ce
count *files*, the way Martin's metrics count packages, so a file importing
another twenty times is one dependency, not twenty.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cgis.cli import app
from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.render.metrics import DuckDBAnalyzer, FileCoupling, file_coupling_metrics
from cgis.storage.sqlite_store import SQLiteStore

runner = CliRunner()


def _file(module: str) -> Node:
    return Node(
        id=module,
        type=NodeType.FILE,
        name=module.rsplit(".", 1)[-1],
        file_path=module.replace(".", "/") + ".py",
        start_line=1,
        end_line=50,
    )


def _func(fqn: str, module: str, namespace: NodeNamespace = NodeNamespace.INTERNAL) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FUNCTION,
        name=fqn.rsplit(".", 1)[-1],
        file_path=module.replace(".", "/") + ".py",
        start_line=2,
        end_line=3,
        namespace=namespace,
    )


def _edge(source: str, target: str, kind: EdgeType = EdgeType.CALLS) -> Edge:
    return Edge(id=f"{source}->{target}:{kind.value}", source=source, target=target, type=kind)


@pytest.fixture
def db(tmp_path: Path) -> str:
    """`core` is depended on by `api` and `cli`; `cli` depends on both; `lone` links to nothing.

    `api` → `core` is joined by three edges (an import and two calls); it must
    count once. Edges to stdlib, to a virtual node, within one file, and of a
    non-dependency type must not count at all.
    """
    nodes = [
        _file("pkg.core"),
        _func("pkg.core.load", "pkg.core"),
        _func("pkg.core.save", "pkg.core"),
        _file("pkg.api"),
        _func("pkg.api.handler", "pkg.api"),
        _file("pkg.cli"),
        _func("pkg.cli.main", "pkg.cli"),
        _file("pkg.lone"),
        _func("pkg.lone.alone", "pkg.lone"),
        _file("tests.test_core"),
        _func("tests.test_core.test_load", "tests.test_core"),
        _func("json.dumps", "json", NodeNamespace.STDLIB),
        Node(
            id="pkg.missing.thing",
            type=NodeType.FUNCTION,
            name="thing",
            file_path=VIRTUAL_FILE_PATH,
            start_line=0,
            end_line=0,
        ),
    ]
    edges = [
        _edge("pkg.api", "pkg.core", EdgeType.IMPORTS),
        _edge("pkg.api.handler", "pkg.core.load"),
        _edge("pkg.api.handler", "pkg.core.save"),
        _edge("pkg.cli.main", "pkg.api.handler"),
        _edge("pkg.cli.main", "pkg.core.load"),
        _edge("pkg.core.load", "pkg.core.save"),  # same file
        _edge("pkg.core.load", "json.dumps"),  # stdlib
        _edge("pkg.lone.alone", "pkg.missing.thing"),  # virtual
        _edge("pkg.lone", "pkg.lone.alone", EdgeType.CONTAINS),  # not a dependency
        _edge("tests.test_core.test_load", "pkg.core.load"),
    ]
    path = str(tmp_path / "graph.db")
    with SQLiteStore(path) as store:
        store.save_graph(nodes, edges)
    return path


def _by_module(rows: list[FileCoupling]) -> dict[str, tuple[int, int, float | None]]:
    return {r.module: (r.afferent, r.efferent, r.instability) for r in rows}


def test_ca_and_ce_count_files_not_edges(db: str) -> None:
    with DuckDBAnalyzer(db) as analyzer:
        rows = _by_module(file_coupling_metrics(analyzer, limit=20))
    assert rows["pkg.core"] == (3, 0, 0.0)  # api, cli, tests — api's three edges count once
    assert rows["pkg.api"] == (1, 1, 0.5)
    assert rows["pkg.cli"] == (0, 2, 1.0)


def test_a_file_linked_to_nothing_has_no_instability(db: str) -> None:
    """Edges to stdlib, a virtual node or within the file are not dependencies on a file."""
    with DuckDBAnalyzer(db) as analyzer:
        rows = _by_module(file_coupling_metrics(analyzer, limit=20))
    assert rows["pkg.lone"] == (0, 0, None)


def test_one_row_per_file_even_beside_a_module_node(tmp_path: Path) -> None:
    """A MODULE node sharing a file's path is a directory or domain row, not a second file."""
    nodes = [
        _file("pkg.core"),
        _func("pkg.core.load", "pkg.core"),
        _file("pkg.api"),
        _func("pkg.api.handler", "pkg.api"),
        Node(
            id="pkg.core_package",
            type=NodeType.MODULE,
            name="core_package",
            file_path="pkg/core.py",
            start_line=0,
            end_line=0,
        ),
    ]
    path = str(tmp_path / "graph.db")
    with SQLiteStore(path) as store:
        store.save_graph(nodes, [_edge("pkg.api.handler", "pkg.core.load")])
    with DuckDBAnalyzer(path) as analyzer:
        rows = file_coupling_metrics(analyzer, limit=20)
    assert sorted(r.module for r in rows) == ["pkg.api", "pkg.core"]
    assert [r.file_path for r in rows].count("pkg/core.py") == 1


def test_ranked_by_ca_plus_ce_then_module(db: str) -> None:
    with DuckDBAnalyzer(db) as analyzer:
        rows = file_coupling_metrics(analyzer, limit=3)
    assert [r.module for r in rows] == ["pkg.core", "pkg.api", "pkg.cli"]


def test_scope_ranks_its_files_but_counts_dependents_from_outside(db: str) -> None:
    # tests.test_core is outside the scope and still counts towards core's Ca.
    with DuckDBAnalyzer(db) as analyzer:
        rows = _by_module(file_coupling_metrics(analyzer, limit=20, scope=["pkg.core"]))
    assert rows == {"pkg.core": (3, 0, 0.0)}


def test_exclude_drops_files_from_the_ranking(db: str) -> None:
    with DuckDBAnalyzer(db) as analyzer:
        rows = _by_module(file_coupling_metrics(analyzer, limit=20, exclude=["tests"]))
    assert "tests.test_core" not in rows
    assert rows["pkg.core"][0] == 3  # ranking only: its dependents are still counted


def test_the_report_and_cli_carry_it(db: str) -> None:
    with DuckDBAnalyzer(db) as analyzer:
        report = analyzer.architecture_report(file_limit=2)
    assert [f.module for f in report.file_coupling] == ["pkg.core", "pkg.api"]

    text = runner.invoke(app, ["metrics", "--db", db])
    assert text.exit_code == 0, text.output
    assert "File coupling" in text.stdout

    raw = runner.invoke(app, ["metrics", "--db", db, "--format", "json"])
    assert raw.exit_code == 0, raw.output
    first = json.loads(raw.stdout)["file_coupling"][0]
    assert first == {
        "module": "pkg.core",
        "file_path": "pkg/core.py",
        "afferent": 3,
        "efferent": 0,
        "instability": 0.0,
    }
