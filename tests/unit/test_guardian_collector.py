"""Unit tests for graph-aware ContextCollector."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.extractors.registry import build_extractors
from cgis.guardian.chunker import Chunk
from cgis.guardian.collector import ContextCollector, parse_features
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore


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


def test_get_git_diff_cached_single_subprocess(tmp_path: Path) -> None:
    """A second get_git_diff() call reuses the cache — one review run needs it twice."""
    collector = ContextCollector(project_root=tmp_path)
    completed = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="DIFF", stderr="")
    with patch("cgis.guardian.collector.subprocess.run", return_value=completed) as mock_run:
        assert collector.get_git_diff() == "DIFF"
        assert collector.get_git_diff() == "DIFF"
    assert mock_run.call_count == 1


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
    with patch.object(collector, "get_changed_source_files", return_value=[]):
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
        patch.object(collector, "get_changed_source_files", return_value=["src/cgis/pipeline.py"]),
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
        patch.object(collector, "get_changed_source_files", return_value=["lib/pkg/mod.py"]),
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
    assert collector.get_changed_source_files() == ["a.py"]


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
    with patch.object(collector, "get_changed_source_files", return_value=["small.py"]):
        result = collector.collect_full_files()
    assert "#### `small.py`" in result
    assert "x = 1" in result


def test_collect_full_files_per_file_line_cap(tmp_path: Path) -> None:
    """A file over the per-file line cap is omitted with an explicit note."""
    (tmp_path / "big.py").write_text("x = 1\n" * 1300)
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_source_files", return_value=["big.py"]):
        result = collector.collect_full_files()
    assert "file omitted: too large (big.py)" in result
    assert "```python" not in result


def test_collect_full_files_exact_cap_included(tmp_path: Path) -> None:
    """A file with exactly _MAX_FILE_LINES lines is included (boundary, not off-by-one)."""
    (tmp_path / "edge.py").write_text("x = 1\n" * 1200)
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_source_files", return_value=["edge.py"]):
        result = collector.collect_full_files()
    assert "#### `edge.py`" in result
    assert "file omitted" not in result


def test_collect_full_files_global_budget_smallest_first(tmp_path: Path) -> None:
    """The global char budget fills smallest-first; the overflow file gets a note."""
    (tmp_path / "tiny.py").write_text("a = 1\n")
    (tmp_path / "mid.py").write_text(("y" * 200 + "\n") * 1000)  # ~201K chars, 1000 lines
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_source_files", return_value=["mid.py", "tiny.py"]):
        result = collector.collect_full_files()
    assert "#### `tiny.py`" in result
    assert "file omitted: budget exhausted (mid.py)" in result


def test_collect_full_files_skips_deleted(tmp_path: Path) -> None:
    """A changed file that no longer exists on HEAD (deleted) is skipped silently."""
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_source_files", return_value=["gone.py"]):
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
            patch.object(collector, "get_changed_source_files", return_value=["a.py"]),
        ):
            context = collector.collect_all()
        assert ("full_files" in context) == (collector is on)


def test_flow_fallback_on_empty_impact(tmp_db: Path) -> None:
    """Empty impact graph + 'flow' feature falls back to the outbound flow graph."""
    node = _make_node("cgis.newmod.func", "src/cgis/newmod.py")
    mock_engine = MagicMock()
    mock_engine.get_impact_graph.return_value = ([], [])
    mock_engine.get_flow_graph.return_value = ([node], [])

    collector = ContextCollector(
        project_root=tmp_db.parent, db_path=tmp_db, features=frozenset({"flow"})
    )
    with (
        patch.object(collector, "get_changed_source_files", return_value=["src/cgis/newmod.py"]),
        patch("cgis.guardian.collector.SQLiteStore") as mock_store_cls,
        patch("cgis.guardian.collector.QueryEngine", return_value=mock_engine),
    ):
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=False)
        result = collector.collect_graph_context()

    assert "Dependency graph (outbound) for `cgis.newmod`" in result
    mock_engine.get_flow_graph.assert_called_once_with("cgis.newmod", max_depth=2)
    assert collector.graph_stats["flow_fallback"] == 1


def test_no_flow_fallback_without_feature(tmp_db: Path) -> None:
    """Without the 'flow' feature the fallback is never attempted (baseline behavior)."""
    mock_engine = MagicMock()
    mock_engine.get_impact_graph.return_value = ([], [])

    collector = ContextCollector(project_root=tmp_db.parent, db_path=tmp_db)
    with (
        patch.object(collector, "get_changed_source_files", return_value=["src/cgis/newmod.py"]),
        patch("cgis.guardian.collector.SQLiteStore") as mock_store_cls,
        patch("cgis.guardian.collector.QueryEngine", return_value=mock_engine),
    ):
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=False)
        collector.collect_graph_context()

    mock_engine.get_flow_graph.assert_not_called()
    assert collector.graph_stats["flow_fallback"] == 0


def test_collect_drift_renders_table(tmp_db: Path, tmp_path: Path) -> None:
    """Drift section renders a markdown table row per domain plus quotient lines."""
    patterns = tmp_path / "docs" / "ontology" / "patterns.yaml"
    patterns.parent.mkdir(parents=True)
    patterns.write_text("patterns: {}\n")

    fake_report = MagicMock()
    fake_report.fqn_prefix = "cgis.query"
    fake_report.expected_pattern = "layered_dag"
    fake_report.drift_score = 0.61
    fake_report.tolerance = 0.50

    fake_scorer = MagicMock()
    fake_domain = MagicMock()
    fake_scorer.load_project_domains.return_value = [fake_domain]
    fake_scorer.load_project_level.return_value = []
    fake_scorer.score.return_value = fake_report

    collector = ContextCollector(
        project_root=tmp_path, db_path=tmp_db, features=frozenset({"drift"})
    )
    with (
        patch("cgis.guardian.collector.DriftScorer", return_value=fake_scorer),
        patch("cgis.guardian.collector.FingerprintExtractor"),
        patch("cgis.guardian.collector.SQLiteStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=False)
        result = collector.collect_drift()

    assert "| cgis.query | layered_dag | 0.61 | 0.50 | ⚠ |" in result


def test_collect_drift_no_domains_returns_empty(tmp_db: Path, tmp_path: Path) -> None:
    """patterns.yaml without domain bindings → empty section, not a header-only table."""
    patterns = tmp_path / "docs" / "ontology" / "patterns.yaml"
    patterns.parent.mkdir(parents=True)
    patterns.write_text("patterns: {}\n")

    fake_scorer = MagicMock()
    fake_scorer.load_project_domains.return_value = []
    fake_scorer.load_project_level.return_value = []

    collector = ContextCollector(
        project_root=tmp_path, db_path=tmp_db, features=frozenset({"drift"})
    )
    with (
        patch("cgis.guardian.collector.DriftScorer", return_value=fake_scorer),
        patch("cgis.guardian.collector.SQLiteStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=False)
        assert collector.collect_drift() == ""


def test_collect_drift_missing_patterns_returns_empty(tmp_db: Path, tmp_path: Path) -> None:
    """No patterns.yaml → empty string, never an exception (spec §4.4)."""
    collector = ContextCollector(
        project_root=tmp_path, db_path=tmp_db, features=frozenset({"drift"})
    )
    assert collector.collect_drift() == ""


def test_collect_drift_swallows_scorer_errors(tmp_db: Path, tmp_path: Path) -> None:
    """A DriftScorer crash degrades to an empty section — guardian never fails a review."""
    patterns = tmp_path / "docs" / "ontology" / "patterns.yaml"
    patterns.parent.mkdir(parents=True)
    patterns.write_text("patterns: {}\n")
    collector = ContextCollector(
        project_root=tmp_path, db_path=tmp_db, features=frozenset({"drift"})
    )
    with patch("cgis.guardian.collector.DriftScorer", side_effect=RuntimeError("boom")):
        assert collector.collect_drift() == ""


def test_parse_features_accepts_chunked() -> None:
    """'chunked' is a valid feature name."""
    assert parse_features("chunked") == frozenset({"chunked"})


def test_collect_for_chunk_diff_is_chunk_diff(tmp_path: Path) -> None:
    """The chunk's own diff slice rides in the context, not the full PR diff."""
    collector = ContextCollector(project_root=tmp_path)
    chunk = Chunk(files=("src/a.py",), diff="diff --git a/src/a.py b/src/a.py\n+x\n")
    context = collector.collect_for_chunk(chunk)
    assert context["diff"] == chunk.diff


def test_collect_for_chunk_full_files_restricted_to_chunk(tmp_path: Path) -> None:
    """Only the chunk's .py files appear in full_files — not other changed files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a = 1\n")
    (tmp_path / "src" / "b.py").write_text("b = 2\n")
    collector = ContextCollector(project_root=tmp_path)
    chunk = Chunk(files=("src/a.py", "README.md"), diff="d")
    context = collector.collect_for_chunk(chunk)
    assert "src/a.py" in context["full_files"]
    assert "src/b.py" not in context["full_files"]


def test_collect_for_chunk_graph_and_stats_accumulate(tmp_path: Path) -> None:
    """Graph context is chunk-scoped (flow fallback ON) and stats sum across chunks."""
    db = tmp_path / "graph.db"
    nodes = [
        Node(
            id="pkg.mod",
            type=NodeType.MODULE,
            name="mod",
            file_path="pkg/mod.py",
            start_line=1,
            end_line=1,
        ),
        Node(
            id="pkg.other",
            type=NodeType.MODULE,
            name="other",
            file_path="pkg/other.py",
            start_line=1,
            end_line=1,
        ),
    ]
    edges = [Edge(id="e1", source="pkg.other", target="pkg.mod", type=EdgeType.CALLS)]
    with SQLiteStore(str(db)) as store:
        store.save_graph(nodes, edges, overwrite=True)
    collector = ContextCollector(project_root=tmp_path, db_path=db, source_root="src")
    ctx1 = collector.collect_for_chunk(Chunk(files=("src/pkg/mod.py",), diff="d1"))
    assert "graph_context" in ctx1  # impact graph: pkg.other -> pkg.mod
    collector.collect_for_chunk(Chunk(files=("src/pkg/ghost.py",), diff="d2"))
    assert collector.graph_stats["total"] == 2  # accumulated, not overwritten
    assert collector.graph_stats["with_graph"] == 1


def test_collect_graph_context_unchanged_without_db(tmp_path: Path) -> None:
    """Regression: the global path still leaves stats at zero when db is missing."""
    collector = ContextCollector(project_root=tmp_path)
    assert collector.collect_graph_context() == ""
    assert collector.graph_stats == {"total": 0, "with_graph": 0, "flow_fallback": 0}


def test_collect_graph_context_skipped_when_include_graph_false(tmp_path: Path) -> None:
    """include_graph=False short-circuits before touching the db (diff-only prompt)."""
    db = tmp_path / "graph.db"
    db.write_text("")  # exists, so only the flag can be why graph is skipped
    collector = ContextCollector(project_root=tmp_path, db_path=db, include_graph=False)
    with patch.object(collector, "_graph_sections") as graph_sections:
        assert collector.collect_graph_context() == ""
        graph_sections.assert_not_called()


# ---------------------------------------------------------------------------
# TypeScript context, end to end against a real ingested graph (#344)
# ---------------------------------------------------------------------------

_TS_HANDLER = "export function helper(value: number): number {\n  return value * 2;\n}\n"
_TS_MAIN = (
    "import { helper } from './handler';\n"
    "export function run(): number {\n  return helper(21);\n}\n"
)


def _ingest_ts_project(tmp_path: Path) -> Path:
    """A two-file TS project ingested into a real graph.db; returns the db path.

    `main.ts` imports `handler.ts`, so `app.handler` has an inbound IMPORTS edge
    and therefore a non-empty *impact* graph — the section the collector builds.
    """
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "handler.ts").write_text(_TS_HANDLER, encoding="utf-8")
    (tmp_path / "app" / "main.ts").write_text(_TS_MAIN, encoding="utf-8")
    db = tmp_path / "graph.db"
    with SQLiteStore(str(db)) as store:
        IngestionPipeline(build_extractors([])).run(str(tmp_path), store=store)
    return db


def test_changed_ts_file_gets_an_impact_graph(tmp_path: Path) -> None:
    """The acceptance criterion of #344, against real ingested nodes.

    Before this, `get_changed_py_files` dropped the file before it was ever
    looked up — and had the suffix filter been lifted alone, the Python FQN
    helper would have derived `app.handler.ts`, matched nothing, logged at
    debug, and produced the same empty section while looking healthy.
    """
    db = _ingest_ts_project(tmp_path)
    collector = ContextCollector(project_root=tmp_path, db_path=db)
    with patch.object(collector, "get_changed_source_files", return_value=["app/handler.ts"]):
        graph = collector.collect_graph_context()
    assert "```mermaid" in graph
    assert "app.handler" in graph
    assert collector.graph_stats == {"total": 1, "with_graph": 1, "flow_fallback": 0}


def test_ts_coverage_footer_counts_the_file_even_when_the_graph_is_empty(tmp_path: Path) -> None:
    """`with_graph == 0` on a TS PR must be visible, not silent.

    A file the graph does not know is still counted in `total`, so the footer
    shows 0/1 rather than omitting the file and showing nothing at all.
    """
    db = _ingest_ts_project(tmp_path)
    collector = ContextCollector(project_root=tmp_path, db_path=db)
    with patch.object(collector, "get_changed_source_files", return_value=["app/absent.ts"]):
        assert collector.collect_graph_context() == ""
    assert collector.graph_stats["total"] == 1
    assert collector.graph_stats["with_graph"] == 0


@pytest.mark.parametrize(
    ("name", "fence"),
    [("handler.ts", "ts"), ("Widget.tsx", "tsx"), ("mod.py", "python")],
)
def test_full_files_fences_each_language_as_itself(tmp_path: Path, name: str, fence: str) -> None:
    """The fence was hardcoded `python`, so TS reached the model mislabelled."""
    (tmp_path / name).write_text("const x = 1;\n", encoding="utf-8")
    collector = ContextCollector(project_root=tmp_path)
    assert f"```{fence}\n" in collector.collect_full_files([name])


def test_chunked_path_keeps_ts_files(tmp_path: Path) -> None:
    """`collect_for_chunk` had its own independent `.py` filter (collector.py:295)."""
    db = _ingest_ts_project(tmp_path)
    collector = ContextCollector(project_root=tmp_path, db_path=db)
    chunk = Chunk(files=["app/handler.ts", "README.md"], diff="diff --git a/app/handler.ts")
    context = collector.collect_for_chunk(chunk)
    assert "app.handler" in context["graph_context"]
    assert "```ts\n" in context["full_files"]
    assert "README.md" not in context["full_files"]
