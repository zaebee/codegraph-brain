"""Unit tests for the shared drift-analysis service."""

from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift_service import DriftAnalysis, analyze_drift
from cgis.storage.sqlite_store import SQLiteStore

_YAML = """\
version: "1.0.0"
drift_weights:
  hub_count:        0.15
  star_count:       0.15
  chain_len:        0.10
  dag_depth:        0.10
  router_count:     0.10
  cycle_ratio:      0.25
  unresolved_ratio: 0.15
patterns:
  pure_utility:
    description: "Hub pattern"
    cycle_ratio:      {max: 0.0}
project_domains:
  - name: "extraction"
    fqn_prefix: "cgis.extractors"
    expected_pattern: pure_utility
    drift_tolerance: 0.15
"""


@pytest.fixture
def graph_db(tmp_path: Path) -> str:
    """A db with two extractor functions, one calling the other."""
    db = str(tmp_path / "g.db")
    nodes = [
        Node(
            id="cgis.extractors.a",
            type=NodeType.FUNCTION,
            name="a",
            file_path="a.py",
            start_line=1,
            end_line=2,
        ),
        Node(
            id="cgis.extractors.b",
            type=NodeType.FUNCTION,
            name="b",
            file_path="b.py",
            start_line=1,
            end_line=2,
        ),
    ]
    edges = [
        Edge(id="e1", source="cgis.extractors.a", target="cgis.extractors.b", type=EdgeType.CALLS)
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


@pytest.fixture
def patterns_file(tmp_path: Path) -> str:
    """Write a minimal patterns YAML file for testing."""
    p = tmp_path / "patterns.yaml"
    p.write_text(_YAML)
    return str(p)


def test_analyze_drift_returns_report_per_domain(graph_db: str, patterns_file: str) -> None:
    """analyze_drift returns one DriftReport per declared project domain."""
    analysis = analyze_drift(graph_db, patterns_file)
    assert isinstance(analysis, DriftAnalysis)
    assert len(analysis.reports) == 1
    assert analysis.reports[0].fqn_prefix == "cgis.extractors"
    assert analysis.quotient == []  # no project_level in YAML


def test_analyze_drift_any_critical_threshold(graph_db: str, patterns_file: str) -> None:
    """any_critical flips based on max_drift threshold."""
    lenient = analyze_drift(graph_db, patterns_file, max_drift=1.0)
    assert lenient.any_critical is False
    strict = analyze_drift(graph_db, patterns_file, max_drift=0.0)
    assert strict.any_critical is True  # any score >= 0.0 trips it


def test_analyze_drift_missing_patterns_raises(graph_db: str, tmp_path: Path) -> None:
    """analyze_drift raises FileNotFoundError when the patterns file is missing."""
    with pytest.raises(FileNotFoundError):
        analyze_drift(graph_db, str(tmp_path / "nope.yaml"))
