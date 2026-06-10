"""Unit tests for graph-aware ContextCollector."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.guardian.collector import ContextCollector, parse_features


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
    assert "cgis.pipeline" in result
    # FQN lookup must strip the ingest source root: `cgis ingest ./src`
    # stores node ids relative to src/, not the repo root.
    mock_engine.get_impact_graph.assert_called_once_with("cgis.pipeline", max_depth=2)


def test_collect_graph_context_custom_source_root(tmp_db: Path) -> None:
    """A non-default source_root is stripped from changed-file paths before lookup."""
    mock_engine = MagicMock()
    mock_engine.get_impact_graph.return_value = ([], [])

    collector = ContextCollector(project_root=tmp_db.parent, db_path=tmp_db, source_root="lib")

    with (
        patch.object(collector, "get_changed_py_files", return_value=["lib/pkg/mod.py"]),
        patch("cgis.guardian.collector.SQLiteStore") as mock_store_cls,
        patch("cgis.guardian.collector.QueryEngine", return_value=mock_engine),
    ):
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=False)
        collector.collect_graph_context()

    mock_engine.get_impact_graph.assert_called_once_with("pkg.mod", max_depth=2)


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


def test_base_ref_overrides_origin_prefix(tmp_path: Path) -> None:
    """base_ref diffs <ref>...HEAD instead of origin/<branch>...HEAD."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=tmp_path, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    (tmp_path / "a.py").write_text("x = 2\n")
    subprocess.run(["git", "commit", "-aqm", "two"], cwd=tmp_path, check=True)

    collector = ContextCollector(project_root=tmp_path, base_ref=base_sha)
    diff = collector.get_git_diff()
    assert "x = 2" in diff
    assert collector.get_changed_py_files() == ["a.py"]


def test_parse_features_valid_and_empty() -> None:
    """parse_features splits, strips, validates; empty string means no features."""
    assert parse_features("") == frozenset()
    assert parse_features("full_files, drift") == frozenset({"full_files", "drift"})


def test_parse_features_unknown_raises() -> None:
    """An unknown feature name fails loud — silent typos would skew ablations."""
    with pytest.raises(ValueError, match="Unknown GUARDIAN_FEATURES"):
        parse_features("full_files,typo")


def test_collector_default_features_empty(tmp_path: Path) -> None:
    """Default ContextCollector has no features enabled (baseline behavior)."""
    collector = ContextCollector(project_root=tmp_path)
    assert collector.features == frozenset()


def test_collect_full_files_reads_changed_files(tmp_path: Path) -> None:
    """Full HEAD text of each changed .py file appears in a fenced block."""
    (tmp_path / "small.py").write_text("x = 1\n")
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_py_files", return_value=["small.py"]):
        result = collector.collect_full_files()
    assert "#### `small.py`" in result
    assert "x = 1" in result


def test_collect_full_files_per_file_line_cap(tmp_path: Path) -> None:
    """A file over the per-file line cap is omitted with an explicit note."""
    (tmp_path / "big.py").write_text("x = 1\n" * 1300)
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_py_files", return_value=["big.py"]):
        result = collector.collect_full_files()
    assert "file omitted: too large (big.py)" in result
    assert "```python" not in result


def test_collect_full_files_global_budget_smallest_first(tmp_path: Path) -> None:
    """The global char budget fills smallest-first; the overflow file gets a note."""
    (tmp_path / "tiny.py").write_text("a = 1\n")
    (tmp_path / "mid.py").write_text(("y" * 200 + "\n") * 1000)  # ~201K chars, 1000 lines
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_py_files", return_value=["mid.py", "tiny.py"]):
        result = collector.collect_full_files()
    assert "#### `tiny.py`" in result
    assert "file omitted: budget exhausted (mid.py)" in result


def test_collect_full_files_skips_deleted(tmp_path: Path) -> None:
    """A changed file that no longer exists on HEAD (deleted) is skipped silently."""
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_py_files", return_value=["gone.py"]):
        assert collector.collect_full_files() == ""


def test_collect_all_full_files_gated_by_feature(tmp_path: Path) -> None:
    """collect_all adds 'full_files' only when the feature flag is on."""
    (tmp_path / "a.py").write_text("z = 3\n")
    base = {"get_git_diff": "diff", "read_file": "content"}
    off = ContextCollector(project_root=tmp_path)
    on = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    for collector in (off, on):
        with (
            patch.object(collector, "get_git_diff", return_value=base["get_git_diff"]),
            patch.object(collector, "read_file", return_value=base["read_file"]),
            patch.object(collector, "get_changed_py_files", return_value=["a.py"]),
        ):
            context = collector.collect_all()
        assert ("full_files" in context) == (collector is on)
