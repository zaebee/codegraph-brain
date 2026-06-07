"""Unit test cases for pipeline."""

import os
from pathlib import Path

import pytest

from cgis.core.models import EdgeType, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore


@pytest.fixture
def pipeline() -> IngestionPipeline:
    return IngestionPipeline({".py": PythonExtractor()})


def test_pipeline_extracts_function(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("def calc_sum(a, b):\n    return a + b\n", encoding="utf-8")

    nodes, raw_edges, _resolved = pipeline.run(str(tmp_path))

    func_nodes = [n for n in nodes if n.type == NodeType.FUNCTION]
    assert len(func_nodes) == 1
    assert func_nodes[0].name == "calc_sum"
    assert not any(e.type == EdgeType.CALLS for e in raw_edges)


def test_pipeline_raises_for_nonexistent_path(pipeline: IngestionPipeline) -> None:
    with pytest.raises(FileNotFoundError):
        pipeline.run("/nonexistent/path/that/does/not/exist")


def test_pipeline_raises_for_file_instead_of_dir(
    pipeline: IngestionPipeline, tmp_path: Path
) -> None:
    file_path = tmp_path / "not_a_dir.py"
    file_path.write_text("x = 1", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        pipeline.run(str(file_path))


def test_pipeline_skips_unknown_extensions(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("some text", encoding="utf-8")
    (tmp_path / "script.py").write_text("def fn(): pass", encoding="utf-8")

    nodes, _, _ = pipeline.run(str(tmp_path))

    func_nodes = [n for n in nodes if n.type == NodeType.FUNCTION]
    assert len(func_nodes) == 1
    assert func_nodes[0].name == "fn"


def test_pipeline_skips_excluded_dirs(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    excluded = tmp_path / "node_modules"
    excluded.mkdir()
    (excluded / "ignored.py").write_text("def should_skip(): pass", encoding="utf-8")
    (tmp_path / "main.py").write_text("def main(): pass", encoding="utf-8")

    nodes, _, _ = pipeline.run(str(tmp_path))

    names = {n.name for n in nodes}
    assert "main" in names
    assert "should_skip" not in names


def test_incremental_skips_unchanged_file(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    """Second incremental run skips a file whose content did not change."""
    (tmp_path / "mod.py").write_text("def alpha(): pass\n", encoding="utf-8")
    db_path = str(tmp_path / "graph.db")

    with SQLiteStore(db_path) as store:
        nodes1, _, _ = pipeline.run(str(tmp_path), store=store)

    # Content unchanged — incremental run should reuse cached nodes
    with SQLiteStore(db_path) as store:
        nodes2, _, _ = pipeline.run(str(tmp_path), store=store)

    assert len(nodes1) == len(nodes2)
    assert {n.name for n in nodes1} == {n.name for n in nodes2}


def test_incremental_reparses_changed_file(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    """Incremental run re-extracts a file whose content changed."""
    py_file = tmp_path / "mod.py"
    py_file.write_text("def alpha(): pass\n", encoding="utf-8")
    db_path = str(tmp_path / "graph.db")

    with SQLiteStore(db_path) as store:
        pipeline.run(str(tmp_path), store=store)

    # Change the file
    py_file.write_text("def alpha(): pass\ndef beta(): pass\n", encoding="utf-8")

    with SQLiteStore(db_path) as store:
        nodes2, _, _ = pipeline.run(str(tmp_path), store=store)

    names = {n.name for n in nodes2}
    assert "alpha" in names
    assert "beta" in names


def test_incremental_removes_stale_file(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    """Nodes from a deleted file are removed from the store on next incremental run."""
    py_file = tmp_path / "transient.py"
    py_file.write_text("def gone(): pass\n", encoding="utf-8")
    (tmp_path / "stable.py").write_text("def stays(): pass\n", encoding="utf-8")
    db_path = str(tmp_path / "graph.db")

    with SQLiteStore(db_path) as store:
        pipeline.run(str(tmp_path), store=store)

    # Delete the file
    py_file.unlink()

    with SQLiteStore(db_path) as store:
        pipeline.run(str(tmp_path), store=store)
        rel_path = py_file.relative_to(tmp_path).as_posix()
        remaining = {n.name for n in store.get_nodes_by_file(rel_path)}

    assert remaining == set()


def test_absolute_and_relative_path_produce_identical_fqns(
    pipeline: IngestionPipeline, tmp_path: Path
) -> None:
    """Passing the same directory as a relative or absolute path must yield identical node IDs."""
    (tmp_path / "mod.py").write_text("def func(): pass\n", encoding="utf-8")

    nodes_abs, _, _ = pipeline.run(str(tmp_path))

    rel_path = os.path.relpath(str(tmp_path))
    nodes_rel, _, _ = pipeline.run(rel_path)

    assert {n.id for n in nodes_abs} == {n.id for n in nodes_rel}


def test_file_paths_stored_as_relative(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    """Nodes and edges must store paths relative to workspace_root, never absolute."""
    (tmp_path / "mod.py").write_text("def func(): pass\n", encoding="utf-8")

    nodes, _, _ = pipeline.run(str(tmp_path))

    for node in nodes:
        assert not Path(node.file_path).is_absolute(), (
            f"Node {node.id!r} has absolute file_path: {node.file_path}"
        )


def test_symlink_outside_workspace_is_skipped(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    """A symlink whose resolved target lies outside workspace_root must be skipped."""
    outside = tmp_path.parent / "external_symlink_target.py"
    outside.write_text("def external_func(): pass\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "local.py").write_text("def local_func(): pass\n", encoding="utf-8")
    (workspace / "outside_link.py").symlink_to(outside)

    nodes, _, _ = pipeline.run(str(workspace))

    names = {n.name for n in nodes}
    assert "local_func" in names
    assert "external_func" not in names


def test_pipeline_logs_and_skips_unparseable_file(
    pipeline: IngestionPipeline, tmp_path: Path
) -> None:
    (tmp_path / "broken.py").write_bytes(b"\xff\xfe invalid utf-8")
    (tmp_path / "good.py").write_text("def fine(): pass", encoding="utf-8")

    nodes, _, _ = pipeline.run(str(tmp_path))

    func_nodes = [n for n in nodes if n.type == NodeType.FUNCTION]
    assert len(func_nodes) == 1
    assert func_nodes[0].name == "fine"
