"""Unit test cases for cli."""

import json
import re
from pathlib import Path

from conftest import (
    fit_patterns_yaml,
    make_chain_db,
    make_file_node,
    make_import_edge,
    module_with_funcs,
    triangle_db,
)
from typer.testing import CliRunner

from cgis.cli import _drift_status_label, app
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors.python_extractor import file_path_to_module_fqn
from cgis.query.engine import QueryEngine
from cgis.storage.sqlite_store import SQLiteStore

runner = CliRunner()


def _plain(text: str) -> str:
    """Strip ANSI escape sequences — Rich/Typer highlights option names even in error messages."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


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

    expected_fqn = f"{file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())}.assist"

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
    result = runner.invoke(
        app,
        [
            "trace",
            f"{file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())}.caller",
            "--db",
            str(db_file),
        ],
    )

    assert result.exit_code == 0
    assert "callee" in result.output


def test_trace_shows_unresolved_external_call(tmp_path: Path) -> None:
    """Calls to built-ins not in the graph are labelled as Unresolved."""
    (tmp_path / "mod.py").write_text("def greet(): print('hi')\n", encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "mod.py"
    result = runner.invoke(
        app,
        [
            "trace",
            f"{file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())}.greet",
            "--db",
            str(db_file),
            "--show-external",
        ],
    )

    assert result.exit_code == 0
    # print() becomes a virtual STDLIB node; check it appears when --show-external is set
    assert "print" in result.output


def test_trace_min_confidence_hides_low_conf_calls(tmp_path: Path) -> None:
    """--min-confidence hides low-confidence (unresolved/raw_call) edges from the tree (#112)."""
    (tmp_path / "mod.py").write_text("def greet(): print('hi')\n", encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])
    fqn = f"{file_path_to_module_fqn('mod.py')}.greet"

    shown = runner.invoke(app, ["trace", fqn, "--db", str(db_file), "--show-external"])
    assert "print" in shown.output

    hidden = runner.invoke(
        app, ["trace", fqn, "--db", str(db_file), "--show-external", "--min-confidence", "0.9"]
    )
    assert hidden.exit_code == 0
    assert "print" not in hidden.output


def test_trace_detects_cycle(tmp_path: Path) -> None:
    """Mutually recursive functions trigger cycle detection in the trace tree."""
    code = "def func_a():\n    func_b()\n\ndef func_b():\n    func_a()\n"
    (tmp_path / "cycle.py").write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "cycle.py"
    fqn = f"{file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())}.func_a"
    result = runner.invoke(app, ["trace", fqn, "--db", str(db_file), "--depth", "5"])

    assert result.exit_code == 0
    assert "Cycle detected" in result.output


def test_impact_renders_callers_in_tree(tmp_path: Path) -> None:
    """impact command shows caller functions in the rich tree."""
    code = "def caller():\n    callee()\n\ndef callee(): pass\n"
    (tmp_path / "funcs.py").write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "funcs.py"
    result = runner.invoke(
        app,
        [
            "impact",
            f"{file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())}.callee",
            "--db",
            str(db_file),
        ],
    )

    assert result.exit_code == 0
    assert "caller" in result.output


def test_impact_shows_unknown_caller_for_missing_node(tmp_path: Path) -> None:
    """Edges whose source node is missing in the graph are labelled as Unknown Caller."""
    db_file = tmp_path / "graph.db"
    with SQLiteStore(str(db_file)) as store:
        target = Node(
            id="mod.target",
            type=NodeType.FUNCTION,
            name="target",
            file_path="mod.py",
            start_line=1,
            end_line=2,
        )
        edge = Edge(
            id="ghost->target",
            source="ghost.node",
            target="mod.target",
            type=EdgeType.CALLS,
        )
        store.save_graph([target], [edge])

    result = runner.invoke(app, ["impact", "mod.target", "--db", str(db_file), "--show-external"])

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
    fqn = f"{file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())}.root_fn"
    result = runner.invoke(app, ["trace", fqn, "--db", str(db_file), "--depth", "1"])

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
    fqn = f"{file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())}.leaf_fn"
    result = runner.invoke(app, ["impact", fqn, "--db", str(db_file), "--depth", "1"])

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
    fqn = f"{file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())}.func_b"
    result = runner.invoke(app, ["impact", fqn, "--db", str(db_file), "--depth", "5"])

    assert result.exit_code == 0
    assert "Cycle detected" in result.output


def test_trace_mermaid_format_outputs_diagram(tmp_path: Path) -> None:
    """trace --format mermaid outputs a valid Mermaid graph definition."""
    code = "def caller():\n    callee()\n\ndef callee(): pass\n"
    (tmp_path / "funcs.py").write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "funcs.py"
    fqn = f"{file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())}.caller"
    result = runner.invoke(app, ["trace", fqn, "--db", str(db_file), "--format", "mermaid"])

    assert result.exit_code == 0
    assert "graph TD" in result.output
    assert "classDef" in result.output


def test_impact_mermaid_format_outputs_diagram(tmp_path: Path) -> None:
    """impact --format mermaid outputs a valid Mermaid graph definition."""
    code = "def caller():\n    callee()\n\ndef callee(): pass\n"
    (tmp_path / "funcs.py").write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    py_file = tmp_path / "funcs.py"
    fqn = f"{file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())}.callee"
    result = runner.invoke(app, ["impact", fqn, "--db", str(db_file), "--format", "mermaid"])

    assert result.exit_code == 0
    assert "graph TD" in result.output
    assert "classDef" in result.output


def test_trace_invalid_format_exits_with_error() -> None:
    """trace --format <unknown> is rejected by Typer with exit code 2."""
    result = runner.invoke(app, ["trace", "some.fqn", "--format", "svg"])

    assert result.exit_code == 2
    assert "Invalid value for '--format'" in _plain(result.output)


def test_impact_invalid_format_exits_with_error() -> None:
    """impact --format <unknown> is rejected by Typer with exit code 2."""
    result = runner.invoke(app, ["impact", "some.fqn", "--format", "svg"])

    assert result.exit_code == 2
    assert "Invalid value for '--format'" in _plain(result.output)


def test_validate_missing_db_exits_with_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", "--db", str(tmp_path / "missing.db")])

    assert result.exit_code == 1
    assert "Database not found" in result.output


def test_validate_passes_when_ratio_below_threshold(tmp_path: Path) -> None:
    """Graph with only resolved edges exits 0."""
    (tmp_path / "mod.py").write_text(
        "def caller():\n    callee()\n\ndef callee(): pass\n", encoding="utf-8"
    )
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    result = runner.invoke(app, ["validate", "--db", str(db_file), "--threshold", "1.0"])

    assert result.exit_code == 0
    assert "Graph Integrity Report" in result.output


def test_validate_fails_when_ratio_exceeds_threshold(tmp_path: Path) -> None:
    """Manually injected raw_call: edge causes validate to fail at threshold 0.0."""
    db_file = tmp_path / "graph.db"
    node = Node(
        id="mod.fn", type=NodeType.FUNCTION, name="fn", file_path="mod.py", start_line=1, end_line=3
    )
    raw_edge = Edge(
        id="e1",
        source="mod.fn",
        target="raw_call:missing_func",
        type=EdgeType.CALLS,
        confidence=0.1,
    )
    with SQLiteStore(str(db_file)) as store:
        store.save_graph([node], [raw_edge], overwrite=True)

    result = runner.invoke(app, ["validate", "--db", str(db_file), "--threshold", "0.0"])

    assert result.exit_code == 1
    assert "exceeds threshold" in result.output


# --- structure command tests ---


def test_structure_missing_db_exits_with_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["structure", "some.module", "--db", str(tmp_path / "no.db")])
    assert result.exit_code == 1
    assert "Database not found" in result.output


def test_structure_unknown_fqn_exits_with_error(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = str(tmp_path / "g.db")
    runner.invoke(app, ["ingest", str(tmp_path), "--output", db])
    result = runner.invoke(app, ["structure", "totally.unknown.fqn", "--db", db])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_structure_text_shows_class_and_methods(tmp_path: Path) -> None:
    """structure command renders CLASS and its METHOD children."""
    (tmp_path / "svc.py").write_text(
        "class Service:\n    def start(self): pass\n    def stop(self): pass\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "g.db")
    runner.invoke(app, ["ingest", str(tmp_path), "--output", db])
    result = runner.invoke(app, ["structure", "svc.Service", "--db", db])
    assert result.exit_code == 0
    assert "Service" in result.output
    assert "start" in result.output
    assert "stop" in result.output


def test_structure_strict_no_calls_edges(tmp_path: Path) -> None:
    """CALLS edges must not appear in structural output."""
    (tmp_path / "svc.py").write_text(
        "class A:\n    def run(self):\n        print('hi')\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "g.db")
    runner.invoke(app, ["ingest", str(tmp_path), "--output", db])

    with SQLiteStore(db) as store:
        nodes, edges = QueryEngine(store).get_structural_graph("svc.A")

    assert all(e.type in (EdgeType.CONTAINS, EdgeType.DECLARES) for e in edges)
    node_ids = {n.id for n in nodes}
    assert "svc.A.run" in node_ids
    assert not any("print" in n.id for n in nodes)


def test_structure_file_path_accepted(tmp_path: Path) -> None:
    """Relative .py path is normalized to FQN automatically."""
    (tmp_path / "mod.py").write_text("class X:\n    def y(self): pass\n", encoding="utf-8")
    db = str(tmp_path / "g.db")
    runner.invoke(app, ["ingest", str(tmp_path), "--output", db])
    # "mod.py" ends with .py → normalised to FQN "mod" which IS in the graph
    result = runner.invoke(app, ["structure", "mod.py", "--db", db])
    assert result.exit_code == 0
    assert "mod" in result.output


def test_structure_extractor_emits_contains_for_top_level_function(tmp_path: Path) -> None:
    """Top-level functions emit a CONTAINS edge from the file node."""
    (tmp_path / "util.py").write_text("def helper(): pass\n", encoding="utf-8")
    db = str(tmp_path / "g.db")
    runner.invoke(app, ["ingest", str(tmp_path), "--output", db])

    with SQLiteStore(db) as store:
        nodes, edges = QueryEngine(store).get_structural_graph("util")

    node_ids = {n.id for n in nodes}
    assert "util.helper" in node_ids
    contains = [e for e in edges if e.type == EdgeType.CONTAINS]
    assert any(e.source == "util" and e.target == "util.helper" for e in contains)


def test_structure_extractor_emits_declares_for_method(tmp_path: Path) -> None:
    """Methods emit a DECLARES edge from the class node."""
    (tmp_path / "svc.py").write_text("class A:\n    def run(self): pass\n", encoding="utf-8")
    db = str(tmp_path / "g.db")
    runner.invoke(app, ["ingest", str(tmp_path), "--output", db])

    with SQLiteStore(db) as store:
        _nodes, edges = QueryEngine(store).get_structural_graph("svc.A")

    declares = [e for e in edges if e.type == EdgeType.DECLARES]
    assert any(e.source == "svc.A" and e.target == "svc.A.run" for e in declares)


def test_validate_shows_top_unresolved(tmp_path: Path) -> None:
    """validate lists top unresolved call targets from raw_call: edges."""
    db_file = tmp_path / "graph.db"
    node = Node(
        id="mod.fn", type=NodeType.FUNCTION, name="fn", file_path="mod.py", start_line=1, end_line=3
    )
    raw_edge = Edge(
        id="e1",
        source="mod.fn",
        target="raw_call:missing_func",
        type=EdgeType.CALLS,
        confidence=0.1,
    )
    with SQLiteStore(str(db_file)) as store:
        store.save_graph([node], [raw_edge], overwrite=True)

    result = runner.invoke(app, ["validate", "--db", str(db_file), "--threshold", "1.0"])

    assert result.exit_code == 0
    assert "missing_func" in result.output


# --- Coverage gap tests ---


def test_ingest_incremental_json_warns_and_falls_back(tmp_path: Path) -> None:
    """--incremental with .json output prints a warning and runs full ingest."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    output = tmp_path / "graph.json"

    result = runner.invoke(app, ["ingest", str(tmp_path), "--output", str(output), "--incremental"])

    assert result.exit_code == 0
    assert "Falling back" in result.output
    assert "Mode" not in result.output  # no incremental table row


def test_ingest_incremental_db_shows_mode_row(tmp_path: Path) -> None:
    """--incremental with .db output prints 'Mode: incremental' in the summary table."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = str(tmp_path / "graph.db")

    result = runner.invoke(app, ["ingest", str(tmp_path), "--output", db, "--incremental"])

    assert result.exit_code == 0
    assert "incremental" in result.output


def test_trace_internal_only_without_mermaid_raises_bad_parameter(tmp_path: Path) -> None:
    """trace --internal-only without --format mermaid exits with BadParameter."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = str(tmp_path / "graph.db")
    runner.invoke(app, ["ingest", str(tmp_path), "--output", db])

    result = runner.invoke(app, ["trace", "mod.fn", "--db", db, "--internal-only"])

    assert result.exit_code == 2
    assert "--internal-only is only supported with '--format mermaid'" in _plain(result.output)


def test_impact_internal_only_without_mermaid_raises_bad_parameter(tmp_path: Path) -> None:
    """impact --internal-only without --format mermaid exits with BadParameter."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = str(tmp_path / "graph.db")
    runner.invoke(app, ["ingest", str(tmp_path), "--output", db])

    result = runner.invoke(app, ["impact", "mod.fn", "--db", db, "--internal-only"])

    assert result.exit_code == 2
    assert "--internal-only is only supported with '--format mermaid'" in _plain(result.output)


def test_trace_mermaid_internal_only_filters_output(tmp_path: Path) -> None:
    """trace --format mermaid --internal-only calls _filter_internal (lines 143-146)."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = str(tmp_path / "graph.db")
    runner.invoke(app, ["ingest", str(tmp_path), "--output", db])

    result = runner.invoke(
        app, ["trace", "mod.fn", "--db", db, "--format", "mermaid", "--internal-only"]
    )

    assert result.exit_code == 0
    # Mermaid output always starts with a graph declaration
    assert "graph" in result.output


def test_trace_tree_shows_unresolved_for_missing_target(tmp_path: Path) -> None:
    """build_trace_tree labels missing targets as Unresolved (lines 174, 179)."""
    db_file = tmp_path / "graph.db"
    caller = Node(
        id="mod.caller",
        type=NodeType.FUNCTION,
        name="caller",
        file_path="mod.py",
        start_line=1,
        end_line=3,
    )
    call_edge = Edge(
        id="e1",
        source="mod.caller",
        target="raw_call:missing",
        type=EdgeType.CALLS,
        confidence=0.1,
    )
    with SQLiteStore(str(db_file)) as store:
        store.save_graph([caller], [call_edge], overwrite=True)

    result = runner.invoke(app, ["trace", "mod.caller", "--db", str(db_file), "--show-external"])

    assert result.exit_code == 0
    assert "Unresolved" in result.output or "raw_call" in result.output


def test_validate_db_read_error_exits_with_error(tmp_path: Path) -> None:
    """validate catches DB errors and exits 1 (lines 352-354)."""
    db_file = tmp_path / "corrupted.db"
    db_file.write_bytes(b"not a sqlite database")

    result = runner.invoke(app, ["validate", "--db", str(db_file)])

    assert result.exit_code == 1
    assert "Error" in result.output


def test_ingest_domains_missing_file_exits_with_error(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["ingest", str(tmp_path), "--output", str(tmp_path / "g.db"), "--domains", "missing.yaml"],
    )
    assert result.exit_code == 1
    assert "not found" in result.output


def test_ingest_full_db_with_domains_runs_uplift(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("class Store: pass\n", encoding="utf-8")
    domains_yaml = tmp_path / "domains.yaml"
    domains_yaml.write_text(
        "version: '0.1.0'\ndomains:\n  StorageLayer:\n"
        "    heuristics:\n      file_path_patterns: ['*mod.py']\n      fqn_patterns: []\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "graph.db")
    result = runner.invoke(
        app, ["ingest", str(tmp_path), "--output", db, "--domains", str(domains_yaml)]
    )
    assert result.exit_code == 0
    with SQLiteStore(db) as store:
        node = store.get_node("mod.Store")
    assert node is not None
    assert "StorageLayer" in node.domains


def test_structure_mermaid_output(tmp_path: Path) -> None:
    """structure --format mermaid emits Mermaid diagram (lines 435-437)."""
    (tmp_path / "mod.py").write_text("class A:\n    def run(self): pass\n", encoding="utf-8")
    db = str(tmp_path / "graph.db")
    runner.invoke(app, ["ingest", str(tmp_path), "--output", db])

    result = runner.invoke(app, ["structure", "mod.A", "--db", db, "--format", "mermaid"])

    assert result.exit_code == 0
    assert "graph" in result.output


def test_ingest_json_output_has_health_metrics(tmp_path: Path) -> None:
    """After ingest to JSON, nodes must contain fan_in/fan_out/depth/in_cycle."""
    runner = CliRunner()
    out = tmp_path / "graph.json"
    result = runner.invoke(app, ["ingest", "src/cgis", "--output", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert len(data["nodes"]) > 0
    node = data["nodes"][0]
    assert "fan_out" in node["metadata"]
    assert "fan_in" in node["metadata"]
    assert "depth" in node["metadata"]
    assert "in_cycle" in node["metadata"]


def test_drift_exits_0_when_all_clean(tmp_path: Path) -> None:
    """cgis drift exits 0 when no project domains are defined (trivially all clean)."""
    db_path = str(tmp_path / "g.db")
    with SQLiteStore(db_path) as store:
        store.save_graph([], [], overwrite=True)

    patterns_path = str(tmp_path / "patterns.yaml")
    Path(patterns_path).write_text(
        "version: '1.0.0'\n"
        "drift_weights:\n"
        "  hub_count: 0.15\n  star_count: 0.15\n  chain_len: 0.10\n"
        "  dag_depth: 0.10\n  router_count: 0.10\n  cycle_ratio: 0.25\n"
        "  unresolved_ratio: 0.15\n"
        "patterns:\n"
        "  pure_utility:\n    description: x\n    cycle_ratio: {max: 0.0}\n"
        "project_domains: []\n"
    )

    result = runner.invoke(app, ["drift", "--db", db_path, "--patterns", patterns_path])
    assert result.exit_code == 0


def test_drift_exits_1_when_any_critical(tmp_path: Path) -> None:
    """cgis drift exits 1 when at least one domain is critical."""
    db_path = str(tmp_path / "g.db")
    with SQLiteStore(db_path) as store:
        store.save_graph([], [], overwrite=True)

    patterns_path = str(tmp_path / "patterns.yaml")
    # hub_count min:10 on an empty domain drives drift to 1.0 → critical
    Path(patterns_path).write_text(
        "version: '1.0.0'\n"
        "drift_weights:\n"
        "  hub_count: 1.0\n  star_count: 0.0\n  chain_len: 0.0\n"
        "  dag_depth: 0.0\n  router_count: 0.0\n  cycle_ratio: 0.0\n"
        "  unresolved_ratio: 0.0\n"
        "patterns:\n"
        "  needs_hub:\n    description: x\n    hub_count: {min: 10}\n"
        "project_domains:\n"
        "  - name: test\n    fqn_prefix: nonexistent\n"
        "    expected_pattern: needs_hub\n    drift_tolerance: 0.10\n"
    )

    result = runner.invoke(app, ["drift", "--db", db_path, "--patterns", patterns_path])
    assert result.exit_code == 1


def test_drift_missing_db_exits_1(tmp_path: Path) -> None:
    """cgis drift exits 1 when --db path does not exist."""
    patterns_path = str(tmp_path / "patterns.yaml")
    Path(patterns_path).write_text(
        "version: '1.0.0'\ndrift_weights: {}\npatterns: {}\nproject_domains: []\n"
    )
    result = runner.invoke(app, ["drift", "--db", "no_such.db", "--patterns", patterns_path])
    assert result.exit_code == 1


def test_drift_missing_patterns_exits_1(tmp_path: Path) -> None:
    """cgis drift exits 1 when --patterns path does not exist."""
    db_path = str(tmp_path / "g.db")
    with SQLiteStore(db_path) as store:
        store.save_graph([], [], overwrite=True)
    result = runner.invoke(app, ["drift", "--db", db_path, "--patterns", "no_such.yaml"])
    assert result.exit_code == 1


def test_drift_json_shape_unchanged(tmp_path: Path) -> None:
    """The --format json payload stays a flat list of report dicts."""
    db = str(tmp_path / "g.db")
    with SQLiteStore(db) as store:
        store.save_graph(
            [
                Node(
                    id="cgis.extractors.a",
                    type=NodeType.FUNCTION,
                    name="a",
                    file_path="a.py",
                    start_line=1,
                    end_line=2,
                )
            ],
            [],
        )
    patterns = tmp_path / "patterns.yaml"
    patterns.write_text(
        'version: "1.0.0"\n'
        "drift_weights:\n  cycle_ratio: 1.0\n"
        "patterns:\n  pure_utility:\n    description: x\n"
        "    cycle_ratio: {max: 0.0}\n"
        "project_domains:\n"
        '  - name: extraction\n    fqn_prefix: "cgis.extractors"\n'
        "    expected_pattern: pure_utility\n    drift_tolerance: 0.15\n"
    )
    result = runner.invoke(
        app, ["drift", "--db", db, "--patterns", str(patterns), "--format", "json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert payload[0]["fqn_prefix"] == "cgis.extractors"


# --- suffix FQN resolution in CLI commands (#145) ---


def _fqn_db(tmp_path: Path) -> str:
    """A db with one caller→callee pair under a deep module path."""
    db = str(tmp_path / "g.db")
    nodes = [
        Node(
            id="src.app.mod.caller",
            type=NodeType.FUNCTION,
            name="caller",
            file_path="mod.py",
            start_line=1,
            end_line=2,
        ),
        Node(
            id="src.app.mod.callee",
            type=NodeType.FUNCTION,
            name="callee",
            file_path="mod.py",
            start_line=3,
            end_line=4,
        ),
    ]
    edges = [
        Edge(id="e", source="src.app.mod.caller", target="src.app.mod.callee", type=EdgeType.CALLS)
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def test_impact_resolves_suffix_with_note(tmp_path: Path) -> None:
    """impact resolves a bare suffix FQN and prints a note about it."""
    db = _fqn_db(tmp_path)
    result = runner.invoke(app, ["impact", "callee", "--db", db])
    assert result.exit_code == 0
    assert "Resolved 'callee'" in result.stdout
    assert "src.app.mod.caller" in result.stdout


def test_trace_ambiguous_exits_with_candidates(tmp_path: Path) -> None:
    """trace exits 1 with an ambiguous error and lists candidates."""
    db = str(tmp_path / "g.db")
    nodes = [
        Node(
            id="a.fn", type=NodeType.FUNCTION, name="fn", file_path="a.py", start_line=1, end_line=2
        ),
        Node(
            id="b.fn", type=NodeType.FUNCTION, name="fn", file_path="b.py", start_line=1, end_line=2
        ),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, [])
    result = runner.invoke(app, ["trace", "fn", "--db", db])
    assert result.exit_code == 1
    assert "Ambiguous" in result.stdout
    assert "a.fn" in result.stdout
    assert "b.fn" in result.stdout


def test_structure_resolves_suffix(tmp_path: Path) -> None:
    """structure resolves a partial suffix FQN to the full FQN."""
    db = _fqn_db(tmp_path)
    result = runner.invoke(app, ["structure", "mod.caller", "--db", db])
    assert result.exit_code == 0
    assert "src.app.mod.caller" in result.stdout


# --- _drift_status_label unit tests (#178) ---


def test_status_label_empty_and_no_signal() -> None:
    """Status-driven labels precede score-driven ones (#178)."""
    assert "EMPTY" in _drift_status_label(status="empty")
    assert "no signal" in _drift_status_label(status="no_signal")
    assert "clean" in _drift_status_label(status="clean")
    assert "critical" in _drift_status_label(status="critical")


def _drift_db_and_patterns(tmp_path: Path, fqn_prefix: str = "nonexistent") -> tuple[str, str]:
    """Helper: empty db + patterns YAML with a single enforced domain."""
    db_path = str(tmp_path / "g.db")
    with SQLiteStore(db_path) as store:
        store.save_graph([], [], overwrite=True)
    patterns_path = str(tmp_path / "patterns.yaml")
    Path(patterns_path).write_text(
        "version: '1.0.0'\n"
        "drift_weights:\n"
        "  hub_count: 1.0\n  star_count: 0.0\n  chain_len: 0.0\n"
        "  dag_depth: 0.0\n  router_count: 0.0\n  cycle_ratio: 0.0\n"
        "  unresolved_ratio: 0.0\n"
        "patterns:\n"
        "  needs_hub:\n    description: x\n    hub_count: {min: 10}\n"
        "project_domains:\n"
        f"  - name: test\n    fqn_prefix: {fqn_prefix}\n"
        "    expected_pattern: needs_hub\n    drift_tolerance: 0.10\n"
    )
    return db_path, patterns_path


def test_drift_empty_domain_output_contains_empty_and_note(tmp_path: Path) -> None:
    """A mis-targeted fqn_prefix produces EMPTY in the output and 'matched 0 nodes' note."""
    db_path, patterns_path = _drift_db_and_patterns(tmp_path, fqn_prefix="totally.missing")
    result = runner.invoke(app, ["drift", "--db", db_path, "--patterns", patterns_path])
    assert result.exit_code == 1
    assert "EMPTY" in result.output
    assert "matched 0 nodes" in result.output


def test_drift_profile_filters_out_python_domain(tmp_path: Path) -> None:
    """--profile typescript skips a domain with profile:python → no EMPTY row for it."""
    db_path = str(tmp_path / "g.db")
    with SQLiteStore(db_path) as store:
        store.save_graph([], [], overwrite=True)
    patterns_path = str(tmp_path / "patterns.yaml")
    Path(patterns_path).write_text(
        "version: '1.0.0'\n"
        "drift_weights:\n"
        "  hub_count: 1.0\n  star_count: 0.0\n  chain_len: 0.0\n"
        "  dag_depth: 0.0\n  router_count: 0.0\n  cycle_ratio: 0.0\n"
        "  unresolved_ratio: 0.0\n"
        "patterns:\n"
        "  needs_hub:\n    description: x\n    hub_count: {min: 10}\n"
        "project_domains:\n"
        "  - name: pydom\n    fqn_prefix: totally.missing\n"
        "    expected_pattern: needs_hub\n    drift_tolerance: 0.10\n"
        "    profile: python\n"
    )
    result = runner.invoke(
        app,
        ["drift", "--db", db_path, "--patterns", patterns_path, "--profile", "typescript"],
    )
    # python-profile domain is filtered out → nothing to score → no EMPTY row
    assert result.exit_code == 0
    assert "EMPTY" not in result.output


def test_find_resolves_partial_name(tmp_path: Path) -> None:
    """cgis find returns the FQN for a partial leaf name; --kind filters (#173)."""
    (tmp_path / "mod.py").write_text("def get_reservation_prices(): pass\n", encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])

    result = runner.invoke(app, ["find", "reservation", "--db", str(db_file)])
    assert result.exit_code == 0
    assert "get_reservation_prices" in result.output

    no_cls = runner.invoke(app, ["find", "reservation", "--db", str(db_file), "--kind", "CLASS"])
    assert "get_reservation_prices" not in no_cls.output


def test_find_query_with_brackets_does_not_crash(tmp_path: Path) -> None:
    """A query with Rich-markup chars (brackets) is escaped, not parsed (#173)."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])
    result = runner.invoke(app, ["find", "List[int]", "--db", str(db_file)])
    assert result.exit_code == 0  # no MarkupError on the unmatched-query message


def _ingest_caller_callee(tmp_path: Path) -> tuple[Path, str]:
    """Ingest a caller→callee module, returning the db path and caller FQN."""
    code = "def caller():\n    callee()\n\ndef callee(): pass\n"
    py_file = tmp_path / "funcs.py"
    py_file.write_text(code, encoding="utf-8")
    db_file = tmp_path / "graph.db"
    runner.invoke(app, ["ingest", str(tmp_path), "--output", str(db_file)])
    module = file_path_to_module_fqn(py_file.relative_to(tmp_path).as_posix())
    return db_file, f"{module}.caller"


def test_trace_json_emits_real_fqns(tmp_path: Path) -> None:
    """`trace --format json` returns parseable {root, nodes, edges} with real FQNs (#171)."""
    db_file, caller_fqn = _ingest_caller_callee(tmp_path)

    result = runner.invoke(app, ["trace", caller_fqn, "--db", str(db_file), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["root"] == caller_fqn
    assert any(n["fqn"] == caller_fqn for n in payload["nodes"])
    assert all({"src", "dst", "type", "confidence"} <= e.keys() for e in payload["edges"])


def test_impact_json_emits_real_fqns(tmp_path: Path) -> None:
    """`impact --format json` returns parseable JSON rooted at the target (#171)."""
    db_file, caller_fqn = _ingest_caller_callee(tmp_path)
    callee_fqn = caller_fqn.replace(".caller", ".callee")

    result = runner.invoke(app, ["impact", callee_fqn, "--db", str(db_file), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["root"] == callee_fqn
    # caller transitively impacts callee → appears as an edge source
    assert any(e["src"] == caller_fqn for e in payload["edges"])


def test_structure_json_emits_real_fqns(tmp_path: Path) -> None:
    """`structure --format json` returns parseable JSON (#171)."""
    db_file, caller_fqn = _ingest_caller_callee(tmp_path)
    module_fqn = caller_fqn.rsplit(".", maxsplit=1)[0]

    result = runner.invoke(app, ["structure", module_fqn, "--db", str(db_file), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["root"] == module_fqn
    assert any(n["fqn"] == caller_fqn for n in payload["nodes"])


def test_trace_internal_only_rejected_for_text_format(tmp_path: Path) -> None:
    """--internal-only is a graph-format flag; using it with text is a usage error (#171)."""
    db_file, caller_fqn = _ingest_caller_callee(tmp_path)

    result = runner.invoke(app, ["trace", caller_fqn, "--db", str(db_file), "--internal-only"])

    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# init-ontology command tests (#174 task 3)
# ---------------------------------------------------------------------------


def test_init_ontology_writes_file_and_summary(tmp_path: Path) -> None:
    """Happy path: writes the yaml, prints a summary, exit 0."""
    db = make_chain_db(tmp_path)
    out = tmp_path / "patterns.yaml"
    result = runner.invoke(app, ["init-ontology", "--db", db, "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert "project_domains" in out.read_text()


def test_init_ontology_refuses_overwrite_without_force(tmp_path: Path) -> None:
    """Existing --out without --force → exit 1, file untouched."""
    db = make_chain_db(tmp_path)
    out = tmp_path / "patterns.yaml"
    out.write_text("hand-tuned: true\n")
    result = runner.invoke(app, ["init-ontology", "--db", db, "--out", str(out)])
    assert result.exit_code == 1
    assert out.read_text() == "hand-tuned: true\n"
    forced = runner.invoke(app, ["init-ontology", "--db", db, "--out", str(out), "--force"])
    assert forced.exit_code == 0
    assert "project_domains" in out.read_text()


def test_init_ontology_missing_db_exits_1(tmp_path: Path) -> None:
    """Missing db → red message, exit 1, no db file created."""
    missing = tmp_path / "none.db"
    result = runner.invoke(
        app, ["init-ontology", "--db", str(missing), "--out", str(tmp_path / "p.yaml")]
    )
    assert result.exit_code == 1
    assert not missing.exists()
    assert "Graph database not found" in result.output


def test_context_emits_xml_payload(tmp_path: Path) -> None:
    """`cgis context` compiles an XML package with focal/callers/callees to stdout."""
    db = make_chain_db(tmp_path)  # app.chain.f0 → … → f11
    result = runner.invoke(app, ["context", "app.chain.f5", "--db", db])
    assert result.exit_code == 0
    assert '<context focal="app.chain.f5"' in result.stdout
    assert "app.chain.f4" in result.stdout  # upstream caller
    assert "app.chain.f6" in result.stdout  # downstream callee
    assert result.stdout.rstrip().endswith("</context>")


def test_context_missing_db_errors(tmp_path: Path) -> None:
    """A missing database is a clear error, not a traceback."""
    result = runner.invoke(app, ["context", "x.y", "--db", str(tmp_path / "missing.db")])
    assert result.exit_code == 1
    assert "Database not found" in result.output


def test_context_unknown_fqn_exits_nonzero(tmp_path: Path) -> None:
    """An FQN absent from the graph exits 1 without emitting a package."""
    db = make_chain_db(tmp_path)
    result = runner.invoke(app, ["context", "app.chain.ghost", "--db", db])
    assert result.exit_code == 1
    assert "<context" not in result.stdout


# ---------------------------------------------------------------------------
# gate_failed rendering + CLI run-level tests (#170 task 3)
# ---------------------------------------------------------------------------


def test_status_label_gate_failed() -> None:
    """gate_failed renders distinctly and precedes score-driven labels (spec §2.4)."""
    label = _drift_status_label(status="gate_failed")
    assert "gate failed" in label


def _intra_cycle_db(tmp_path: Path) -> str:
    """Two-module intra-domain cycle db with ≥ min_nodes total for drift scoring."""
    db = str(tmp_path / "cycle.db")
    nodes = module_with_funcs("app.loop.a", "app/loop/a.py", 6) + module_with_funcs(
        "app.loop.b", "app/loop/b.py", 6
    )
    edges = [
        Edge(id="c1", source="app.loop.a", target="app.loop.b", type=EdgeType.IMPORTS),
        Edge(id="c2", source="app.loop.b", target="app.loop.a", type=EdgeType.IMPORTS),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def _cycle_patterns_yaml(tmp_path: Path, *, with_baseline: bool) -> str:
    """Patterns yaml with a hygiene cycle_ratio {max: 0.0} domain binding.

    The domain is hygiene-only (no expected_pattern) so the only gate is the
    hygiene invariant itself.  With ``with_baseline=True``, the domain declares a
    hygiene_baseline that acknowledges the measured cycle ratio and a tolerance of
    1.0 so the score gate also clears — confirming that acknowledged debt fully
    suppresses both gate_failed and critical.  Without a baseline the hygiene
    breach forces gate_failed and exit 1.
    """
    if with_baseline:
        domain_block = (
            "  - name: loop\n    fqn_prefix: app.loop\n"
            "    drift_tolerance: 1.0\n"
            "    hygiene_baseline:\n      cycle_ratio: 1.0\n"
        )
    else:
        domain_block = "  - name: loop\n    fqn_prefix: app.loop\n    drift_tolerance: 0.50\n"
    patterns_path = str(tmp_path / "patterns.yaml")
    Path(patterns_path).write_text(
        "version: '1.0.0'\n"
        "drift_weights:\n"
        "  hub_count: 0.0\n  star_count: 0.0\n  chain_len: 0.0\n"
        "  dag_depth: 0.0\n  router_count: 0.0\n  cycle_ratio: 1.0\n"
        "  unresolved_ratio: 0.0\n"
        "hygiene:\n"
        "  cycle_ratio: {max: 0.0}\n"
        "  unresolved_ratio: {max: 0.2}\n"
        "patterns:\n"
        "  pure_utility:\n    description: x\n    hub_count: {min: 1}\n"
        "project_domains:\n" + domain_block
    )
    return patterns_path


def test_drift_gate_failed_output_and_exit_1(tmp_path: Path) -> None:
    """An intra-domain cycle with cycle_ratio {max: 0.0} hygiene → gate failed + exit 1."""
    db = _intra_cycle_db(tmp_path)
    patterns = _cycle_patterns_yaml(tmp_path, with_baseline=False)
    result = runner.invoke(app, ["drift", "--db", db, "--patterns", patterns])
    assert result.exit_code == 1
    assert "gate failed" in result.output


def test_drift_acknowledged_baseline_exits_0(tmp_path: Path) -> None:
    """The same cycle db with a covering hygiene_baseline → exit 0 (acknowledged)."""
    db = _intra_cycle_db(tmp_path)
    patterns = _cycle_patterns_yaml(tmp_path, with_baseline=True)
    result = runner.invoke(app, ["drift", "--db", db, "--patterns", patterns])
    assert result.exit_code == 0


def test_metrics_reports_top_nodes(tmp_path: Path) -> None:
    """`cgis metrics` prints architectural metrics for the graph (#16)."""
    db = make_chain_db(tmp_path)  # app.chain.f0 → … → f11 CALLS chain
    result = runner.invoke(app, ["metrics", "--db", db])
    assert result.exit_code == 0
    assert "app.chain.f" in result.stdout


def test_metrics_json_format(tmp_path: Path) -> None:
    """`cgis metrics --format json` emits a parseable architecture report."""
    db = make_chain_db(tmp_path)
    result = runner.invoke(app, ["metrics", "--db", db, "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "bottlenecks" in payload
    assert "god_classes" in payload


def test_metrics_missing_db_errors(tmp_path: Path) -> None:
    """A missing database is a clean error, not a traceback."""
    result = runner.invoke(app, ["metrics", "--db", str(tmp_path / "missing.db")])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_metrics_limit_applies_to_god_classes(tmp_path: Path) -> None:
    """`--limit` caps the God-class section too, not just bottlenecks (#230 review)."""
    nodes: list[Node] = []
    edges: list[Edge] = []
    for c in range(6):
        nodes.append(
            Node(
                id=f"app.C{c}",
                type=NodeType.CLASS,
                name=f"C{c}",
                file_path="m.py",
                start_line=1,
                end_line=2,
            )
        )
        nodes.append(
            Node(
                id=f"app.C{c}.m",
                type=NodeType.METHOD,
                name="m",
                file_path="m.py",
                start_line=3,
                end_line=4,
            )
        )
        edges.append(
            Edge(id=f"d{c}", source=f"app.C{c}", target=f"app.C{c}.m", type=EdgeType.DECLARES)
        )
    db = str(tmp_path / "classes.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)

    result = runner.invoke(app, ["metrics", "--db", db, "--limit", "2", "--format", "json"])
    assert result.exit_code == 0
    assert len(json.loads(result.stdout)["god_classes"]) == 2


def test_metrics_invalid_db_errors_cleanly(tmp_path: Path) -> None:
    """A present-but-not-SQLite file is a clean ❌, not a DuckDB traceback (#230 review)."""
    bad = tmp_path / "not_a.db"
    bad.write_text("definitely not a sqlite database", encoding="utf-8")
    result = runner.invoke(app, ["metrics", "--db", str(bad)])
    assert result.exit_code == 1
    assert "❌" in result.output


def test_metrics_includes_pagerank_section(tmp_path: Path) -> None:
    """`cgis metrics` shows a PageRank/critical-nodes section (#231)."""
    db = make_chain_db(tmp_path)
    text = runner.invoke(app, ["metrics", "--db", db])
    assert text.exit_code == 0
    assert "PageRank" in text.stdout

    payload = json.loads(runner.invoke(app, ["metrics", "--db", db, "--format", "json"]).stdout)
    assert "critical" in payload
    assert all("page_rank" in m for m in payload["critical"])


# ── fit-quality rendering + roll-ups (#177) ───────────────────────────────────

from cgis.cli import _fit_cell  # noqa: E402
from cgis.query.drift.drift import FitQuality  # noqa: E402


def test_fit_cell_bands() -> None:
    """_fit_cell colours by band and shows '—' for an absent fit."""
    assert _fit_cell(None) == "—"
    good = FitQuality("funnel", 0.10, "layered_dag", 0.4, "good")
    weak = FitQuality("funnel", 0.30, "layered_dag", 0.5, "weak")
    none = FitQuality("funnel", 0.60, "layered_dag", 0.7, "none")
    assert "green" in _fit_cell(good)
    assert "funnel 0.10" in _fit_cell(good)
    assert "yellow" in _fit_cell(weak)
    assert "✗" in _fit_cell(none)
    assert "red" in _fit_cell(none)


def test_drift_reports_no_template_fits_and_coverage(tmp_path: Path) -> None:
    """A 030T domain triggers the 'no template fits' roll-up; unbound code is listed."""
    db = triangle_db(tmp_path)
    # extra unbound package so coverage is non-empty
    with SQLiteStore(db) as store:
        store.save_graph(
            [
                Node(
                    id="orphan.x",
                    type=NodeType.FUNCTION,
                    name="x",
                    file_path="orphan.py",
                    start_line=1,
                    end_line=2,
                )
            ],
            [],
        )
    patterns = tmp_path / "p.yaml"
    patterns.write_text(fit_patterns_yaml())
    result = runner.invoke(
        app, ["drift", "--db", db, "--patterns", str(patterns), "--max-drift", "1.0"]
    )
    assert "no template fits" in result.output
    assert "Unbound code" in result.output
    assert "orphan" in result.output


def _audit_db(tmp_path: Path) -> str:
    """Two handlers reach the check, one (h3) doesn't — the IDOR gap."""
    nodes = [
        Node(
            id="app.h1",
            type=NodeType.ROUTE_HANDLER,
            name="h1",
            file_path="r.py",
            start_line=10,
            end_line=11,
        ),
        Node(
            id="app.h3",
            type=NodeType.ROUTE_HANDLER,
            name="h3",
            file_path="r.py",
            start_line=30,
            end_line=31,
        ),
        Node(
            id="app.verify_owner",
            type=NodeType.FUNCTION,
            name="verify_owner",
            file_path="a.py",
            start_line=5,
            end_line=6,
        ),
        Node(
            id="app.storage",
            type=NodeType.FUNCTION,
            name="storage",
            file_path="s.py",
            start_line=1,
            end_line=2,
        ),
    ]
    edges = [
        Edge(id="e1", source="app.h1", target="app.verify_owner", type=EdgeType.CALLS),
        Edge(id="e2", source="app.h3", target="app.storage", type=EdgeType.CALLS),
    ]
    db = str(tmp_path / "audit.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def test_audit_lists_gaps_and_exits_nonzero(tmp_path: Path) -> None:
    """`cgis audit` lists the uncovered handler and exits 1 so CI can gate (#172)."""
    db = _audit_db(tmp_path)
    result = runner.invoke(
        app, ["audit", "verify_owner", "--from-type", "ROUTE_HANDLER", "--db", db]
    )
    assert result.exit_code == 1
    assert "app.h3" in result.stdout
    assert "1" in result.stdout  # gap count


def test_audit_json_format(tmp_path: Path) -> None:
    """`cgis audit --format json` emits parseable covered/gaps with the resolved target."""
    db = _audit_db(tmp_path)
    result = runner.invoke(
        app,
        ["audit", "verify_owner", "--from-type", "ROUTE_HANDLER", "--db", db, "--format", "json"],
    )
    payload = json.loads(result.stdout)
    assert payload["target"] == "app.verify_owner"
    assert {g["fqn"] for g in payload["gaps"]} == {"app.h3"}
    assert {c["fqn"] for c in payload["covered"]} == {"app.h1"}


def test_audit_requires_a_selector(tmp_path: Path) -> None:
    """Without --from-type or --from-prefix the command errors (exit 2)."""
    db = _audit_db(tmp_path)
    result = runner.invoke(app, ["audit", "verify_owner", "--db", db])
    assert result.exit_code == 2
    assert "from-type" in result.output


# --- suggest-packages command tests ---


def _suggest_db(tmp_path: Path) -> str:
    """A db with two well-separated clusters under prefix 'p' (verdict: split)."""
    files = [make_file_node(f"p.{n}") for n in ("a", "b", "c", "x", "y", "z")]
    edges = [
        make_import_edge(f"p.{s}", f"p.{t}")
        for grp in (("a", "b", "c"), ("x", "y", "z"))
        for s in grp
        for t in grp
        if s != t
    ]
    db = str(tmp_path / "suggest.db")
    with SQLiteStore(db) as store:
        store.save_graph(files, edges)
    return db


def test_suggest_packages_json(tmp_path: Path) -> None:
    """suggest-packages --format json exits 0 and returns a valid report dict."""
    db = _suggest_db(tmp_path)
    result = runner.invoke(app, ["suggest-packages", "p", "--db", db, "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["package"] == "p"
    valid_verdicts = {"split", "borderline", "aligned", "leave", "consolidate", "no_signal"}
    assert payload["verdict"] in valid_verdicts
    assert "direction" in payload
    assert "modularity_q" in payload


def test_suggest_packages_missing_db(tmp_path: Path) -> None:
    """suggest-packages exits 1 and prints 'not found' when --db is absent."""
    result = runner.invoke(app, ["suggest-packages", "p", "--db", str(tmp_path / "nope.db")])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()
