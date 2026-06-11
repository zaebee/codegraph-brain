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

# YAML for the observe-only quotient test.  Three hygiene-only domains (no
# expected_pattern → zero drift score) plus a project_level binding with
# enforce: false whose quotient graph scores ≈0.538 (030T-dominant triangle
# vs pipeline_stage 021C ideal; empirically measured 7/13 ≈ 0.5385).
_YAML_WITH_OBSERVE_ONLY_QUOTIENT = """\
version: "1.0.0"
profiles:
  python:
    drift_weights:
      hub_count:        0.15
      star_count:       0.15
      chain_len:        0.10
      dag_depth:        0.10
      router_count:     0.10
      cycle_ratio:      0.25
      unresolved_ratio: 0.15
    layers:
      imports: 0.35
      calls:   0.35
      gates:   0.30
    triad_weights: {}
patterns:
  pipeline_stage:
    description: "Sequential"
    ideal:
      imports: {"021C": 1.0}
      calls:   {"021C": 1.0}
hygiene:
  cycle_ratio:      {max: 0.0}
  unresolved_ratio: {max: 0.2}
project_domains:
  - name: "alpha"
    fqn_prefix: "dom.alpha"
    drift_tolerance: 0.99
  - name: "beta"
    fqn_prefix: "dom.beta"
    drift_tolerance: 0.99
  - name: "gamma"
    fqn_prefix: "dom.gamma"
    drift_tolerance: 0.99
project_level:
  - name: "whole"
    fqn_prefix: "quotient"
    expected_pattern: pipeline_stage
    profile: python
    drift_tolerance: 0.15
    enforce: false
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


def test_quotient_observe_only_does_not_flip_any_critical(tmp_path: Path) -> None:
    """Quotient with enforce:false does not set any_critical even when its score exceeds max_drift.

    Graph topology: three hygiene-only domains (alpha, beta, gamma) connected in a
    fully-closed triangle (alpha→beta, alpha→gamma, beta→gamma CALLS edges).  The
    three domain reports each score 0.0 (no expected_pattern → no constraints).
    The quotient layer collapses those domains to three nodes; the resulting 030T
    triad diverges sharply from the pipeline_stage ideal (021C), yielding an
    empirically measured drift_score of 7/13 ≈ 0.538.

    With max_drift=0.3 the quotient score exceeds the threshold, but because
    its binding declares enforce:false it must NOT contribute to any_critical.
    The three domain reports all score 0.0 < 0.3 so they also leave any_critical
    False — proving that observe-only bindings are truly inert.
    """
    # Arrange: write the patterns YAML and build the SQLite graph.
    patterns_file = tmp_path / "patterns.yaml"
    patterns_file.write_text(_YAML_WITH_OBSERVE_ONLY_QUOTIENT)

    db = str(tmp_path / "g.db")
    nodes = [
        Node(
            id="dom.alpha.x",
            type=NodeType.FUNCTION,
            name="x",
            file_path="x.py",
            start_line=1,
            end_line=2,
        ),
        Node(
            id="dom.beta.y",
            type=NodeType.FUNCTION,
            name="y",
            file_path="y.py",
            start_line=1,
            end_line=2,
        ),
        Node(
            id="dom.gamma.z",
            type=NodeType.FUNCTION,
            name="z",
            file_path="z.py",
            start_line=1,
            end_line=2,
        ),
    ]
    # Three cross-domain CALLS form a complete triangle (030T at quotient level).
    edges = [
        Edge(id="e1", source="dom.alpha.x", target="dom.beta.y", type=EdgeType.CALLS),
        Edge(id="e2", source="dom.alpha.x", target="dom.gamma.z", type=EdgeType.CALLS),
        Edge(id="e3", source="dom.beta.y", target="dom.gamma.z", type=EdgeType.CALLS),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)

    # Act: run with max_drift=0.3, which sits below the quotient score but above domain scores.
    max_drift = 0.3
    analysis = analyze_drift(db, str(patterns_file), max_drift=max_drift)

    # Assert 1: quotient is populated and its binding is observe-only.
    assert len(analysis.quotient) == 1
    q_binding, q_report = analysis.quotient[0]
    assert q_binding.enforce is False

    # Assert 2: the quotient score genuinely exceeds max_drift (would trip if enforced).
    # Empirically measured: 7/13 ≈ 0.538 (030T-dominant triangle vs 021C ideal).
    assert q_report.drift_score >= max_drift, (
        f"quotient drift_score {q_report.drift_score:.6f} should be >= {max_drift}"
    )

    # Assert 3: any_critical is False — domain scores are 0.0 < max_drift, and the
    # observe-only quotient binding must NOT flip the gate even though its score exceeds it.
    for r in analysis.reports:
        assert r.drift_score < max_drift, f"domain '{r.domain}' unexpectedly exceeded max_drift"
    assert analysis.any_critical is False, (
        "observe-only quotient binding (enforce=False) must not flip any_critical"
    )
