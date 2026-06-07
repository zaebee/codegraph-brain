"""Unit test cases for cli."""

from pathlib import Path

from typer.testing import CliRunner

from cgis.cli import app
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.storage.sqlite_store import SQLiteStore

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "cgis" in result.output


def test_ingest_help() -> None:
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    assert "Scan a repository" in result.output


def test_ingest_to_json(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("def add(a, b): return a + b", encoding="utf-8")
    output = tmp_path / "graph.json"

    result = runner.invoke(app, ["ingest", str(tmp_path), "--output", str(output)])

    assert result.exit_code == 0
    assert "Nodes extracted" in result.output
    assert output.exists()


def test_ingest_to_db(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("def add(a, b): return a + b", encoding="utf-8")
    output = tmp_path / "graph.db"

    result = runner.invoke(app, ["ingest", str(tmp_path), "--output", str(output)])

    assert result.exit_code == 0
    assert output.exists()


def test_ingest_empty_dir_shows_warning(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ingest", str(tmp_path)])

    assert result.exit_code == 0
    assert "Warning" in result.output


def test_ingest_nonexistent_path_exits_with_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ingest", str(tmp_path / "does_not_exist")])

    assert result.exit_code == 1
    assert "Error" in result.output


def test_trace_missing_db_exits_with_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["trace", "some.fqn", "--db", str(tmp_path / "missing.db")])

    assert result.exit_code == 1
    assert "Database not found" in result.output


def test_trace_unknown_fqn_exits_with_error(tmp_path: Path) -> None:
    (tmp_path / "dummy.py").write_text("def noop(): pass", encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    result = runner.invoke(app, ["trace", "nonexistent.fqn", "--db", str(db_file)])

    assert result.exit_code == 1
    assert "Start entity not found" in result.output


def test_impact_missing_db_exits_with_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["impact", "some.fqn", "--db", str(tmp_path / "missing.db")])

    assert result.exit_code == 1
    assert "Database not found" in result.output


def test_impact_unknown_fqn_exits_with_error(tmp_path: Path) -> None:
    (tmp_path / "dummy.py").write_text("def noop(): pass", encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    result = runner.invoke(app, ["impact", "nonexistent.fqn", "--db", str(db_file)])

    assert result.exit_code == 1
    assert "Target entity not found" in result.output


def test_full_workflow_ingest_trace_impact(tmp_path: Path) -> None:
    """End-to-end: ingest -> trace -> impact on a simple function."""
    py_file = tmp_path / "helper.py"
    py_file.write_text("def assist(): pass", encoding="utf-8")
    db_file = tmp_path / "graph.db"

    ingest_result = runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])
    assert ingest_result.exit_code == 0
    assert db_file.exists()

    expected_fqn = f"{py_file}:assist"

    trace_result = runner.invoke(app, ["trace", expected_fqn, "--db", str(db_file)])
    assert trace_result.exit_code == 0
    assert "Tracing execution flow starting from" in trace_result.output

    impact_result = runner.invoke(app, ["impact", expected_fqn, "--db", str(db_file)])
    assert impact_result.exit_code == 0
    assert "Analyzing transitive upstream callers" in impact_result.output


def test_trace_renders_callees_in_tree(tmp_path: Path) -> None:
    """trace command shows called functions in the rich tree."""
    code = "def caller():\n    callee()\n\ndef callee(): pass\n"
    (tmp_path / "funcs.py").write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "funcs.py"
    result = runner.invoke(app, ["trace", f"{py_file}:caller", "--db", str(db_file)])

    assert result.exit_code == 0
    assert "callee" in result.output


def test_trace_shows_unresolved_external_call(tmp_path: Path) -> None:
    """Calls to built-ins not in the graph are labelled as Unresolved."""
    (tmp_path / "mod.py").write_text("def greet(): print('hi')\n", encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "mod.py"
    result = runner.invoke(app, ["trace", f"{py_file}:greet", "--db", str(db_file)])

    assert result.exit_code == 0
    assert "Unresolved" in result.output


def test_trace_detects_cycle(tmp_path: Path) -> None:
    """Mutually recursive functions trigger cycle detection in the trace tree."""
    code = "def func_a():\n    func_b()\n\ndef func_b():\n    func_a()\n"
    (tmp_path / "cycle.py").write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "cycle.py"
    result = runner.invoke(
        app, ["trace", f"{py_file}:func_a", "--db", str(db_file), "--depth", "5"]
    )

    assert result.exit_code == 0
    assert "Cycle detected" in result.output


def test_impact_renders_callers_in_tree(tmp_path: Path) -> None:
    """impact command shows caller functions in the rich tree."""
    code = "def caller():\n    callee()\n\ndef callee(): pass\n"
    (tmp_path / "funcs.py").write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "funcs.py"
    result = runner.invoke(app, ["impact", f"{py_file}:callee", "--db", str(db_file)])

    assert result.exit_code == 0
    assert "caller" in result.output


def test_impact_shows_unknown_caller_for_missing_node(tmp_path: Path) -> None:
    """Edges whose source node is missing in the graph are labelled as Unknown Caller."""
    db_file = tmp_path / "graph.db"
    with SQLiteStore(str(db_file)) as store:
        target = Node(
            id="mod.py:target",
            type=NodeType.FUNCTION,
            name="target",
            file_path="mod.py",
            start_line=1,
            end_line=2,
        )
        edge = Edge(
            id="ghost->target",
            source="ghost.node",
            target="mod.py:target",
            type=EdgeType.CALLS,
        )
        store.save_graph([target], [edge])

    result = runner.invoke(app, ["impact", "mod.py:target", "--db", str(db_file)])

    assert result.exit_code == 0
    assert "Unknown Caller" in result.output


def test_trace_stops_at_max_depth(tmp_path: Path) -> None:
    """Tree builder returns immediately when current_depth >= max_depth."""
    code = (
        "def root_fn():\n    middle_fn()\n\n"
        "def middle_fn():\n    leaf_fn()\n\n"
        "def leaf_fn(): pass\n"
    )
    (tmp_path / "chain.py").write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "chain.py"
    result = runner.invoke(
        app, ["trace", f"{py_file}:root_fn", "--db", str(db_file), "--depth", "1"]
    )

    # Rich may wrap long FQN strings across lines — join without separator to recover names
    flat = "".join(result.output.split())
    assert result.exit_code == 0
    assert "middle_fn" in flat
    assert "leaf_fn" not in flat


def test_impact_stops_at_max_depth(tmp_path: Path) -> None:
    """Impact tree builder returns immediately when current_depth >= max_depth."""
    code = (
        "def root_fn():\n    middle_fn()\n\n"
        "def middle_fn():\n    leaf_fn()\n\n"
        "def leaf_fn(): pass\n"
    )
    (tmp_path / "chain.py").write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "chain.py"
    result = runner.invoke(
        app, ["impact", f"{py_file}:leaf_fn", "--db", str(db_file), "--depth", "1"]
    )

    flat = "".join(result.output.split())
    assert result.exit_code == 0
    assert "middle_fn" in flat
    assert "root_fn" not in flat


def test_impact_detects_cycle(tmp_path: Path) -> None:
    """Mutually recursive functions trigger cycle detection in the impact tree."""
    code = "def func_a():\n    func_b()\n\ndef func_b():\n    func_a()\n"
    (tmp_path / "cycle.py").write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "cycle.py"
    result = runner.invoke(
        app, ["impact", f"{py_file}:func_b", "--db", str(db_file), "--depth", "5"]
    )

    assert result.exit_code == 0
    assert "Cycle detected" in result.output
