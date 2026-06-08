"""Unit tests for graph-aware ContextCollector."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.guardian.collector import ContextCollector


def _make_node(fqn: str, file_path: str = "src/cgis/pipeline.py") -> Node:
    return Node(
        id=fqn,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        type=NodeType.METHOD,
        file_path=file_path,
        start_line=1,
        end_line=10,
        namespace=NodeNamespace.INTERNAL,
    )


def _make_edge(source: str, target: str) -> Edge:
    return Edge(
        id=f"{source}->{target}",
        source=source,
        target=target,
        type=EdgeType.CALLS,
    )


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Return a path to a fake graph.db (just needs to exist)."""
    db = tmp_path / "graph.db"
    db.touch()
    return db


def test_collect_graph_context_no_db(tmp_path: Path) -> None:
    """When db_path is None, collect_graph_context returns empty string."""
    collector = ContextCollector(project_root=tmp_path, db_path=None)
    assert collector.collect_graph_context() == ""


def test_collect_graph_context_missing_db(tmp_path: Path) -> None:
    """When db_path points to a nonexistent file, collect_graph_context returns empty string."""
    collector = ContextCollector(project_root=tmp_path, db_path=tmp_path / "missing.db")
    assert collector.collect_graph_context() == ""


def test_collect_graph_context_no_changed_files(tmp_db: Path) -> None:
    """When no .py files changed, collect_graph_context returns empty string."""
    collector = ContextCollector(project_root=tmp_db.parent, db_path=tmp_db)
    with patch.object(collector, "get_changed_py_files", return_value=[]):
        assert collector.collect_graph_context() == ""


def test_collect_graph_context_injects_mermaid(tmp_db: Path) -> None:
    """Graph context is built when changed files have impact graph nodes."""
    node_a = _make_node("src.cgis.pipeline.run", "src/cgis/pipeline.py")
    node_b = _make_node("src.cgis.cli.ingest", "src/cgis/cli.py")
    edge = _make_edge("src.cgis.cli.ingest", "src.cgis.pipeline.run")

    mock_store = MagicMock()
    mock_engine = MagicMock()
    mock_engine.get_impact_graph.return_value = ([node_a, node_b], [edge])

    collector = ContextCollector(project_root=tmp_db.parent, db_path=tmp_db)

    with (
        patch.object(collector, "get_changed_py_files", return_value=["src/cgis/pipeline.py"]),
        patch("cgis.guardian.collector.SQLiteStore") as mock_store_cls,
        patch("cgis.guardian.collector.QueryEngine", return_value=mock_engine),
    ):
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=mock_store)
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = collector.collect_graph_context()

    assert "```mermaid" in result
    assert "src.cgis.pipeline" in result
    mock_engine.get_impact_graph.assert_called_once_with("src.cgis.pipeline", max_depth=2)


def test_collect_all_includes_graph_key(tmp_db: Path) -> None:
    """collect_all() adds 'graph_context' key when graph data is available."""
    collector = ContextCollector(project_root=tmp_db.parent, db_path=tmp_db)
    with (
        patch.object(collector, "get_git_diff", return_value="diff"),
        patch.object(collector, "read_file", return_value="content"),
        patch.object(collector, "collect_graph_context", return_value="### Impact graph..."),
    ):
        context = collector.collect_all()

    assert "graph_context" in context
    assert context["graph_context"] == "### Impact graph..."


def test_collect_all_omits_graph_key_when_empty(tmp_path: Path) -> None:
    """collect_all() does not add 'graph_context' key when graph returns empty."""
    collector = ContextCollector(project_root=tmp_path, db_path=None)
    with (
        patch.object(collector, "get_git_diff", return_value="diff"),
        patch.object(collector, "read_file", return_value="content"),
    ):
        context = collector.collect_all()

    assert "graph_context" not in context
