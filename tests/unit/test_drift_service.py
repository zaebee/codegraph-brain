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


def test_analyze_drift_missing_db_raises(tmp_path: Path, patterns_file: str) -> None:
    """analyze_drift refuses to silently create a missing database."""
    with pytest.raises(FileNotFoundError):
        analyze_drift(str(tmp_path / "missing.db"), patterns_file)


def test_analyze_drift_bad_suffix_raises(graph_db: str, tmp_path: Path) -> None:
    """analyze_drift rejects patterns_path whose suffix is not .yaml or .yml."""
    bad = tmp_path / "patterns.txt"
    bad.write_text(_YAML)
    with pytest.raises(ValueError, match=r"patterns_path must be a \.yaml or \.yml file"):
        analyze_drift(graph_db, str(bad))


def test_analyze_drift_uppercase_suffix_accepted(graph_db: str, tmp_path: Path) -> None:
    """analyze_drift matches the suffix case-insensitively (.YAML is valid)."""
    p = tmp_path / "patterns.YAML"
    p.write_text(_YAML)
    analysis = analyze_drift(graph_db, str(p))
    assert isinstance(analysis, DriftAnalysis)
    assert len(analysis.reports) == 1


def test_analyze_drift_resolved_path_works(graph_db: str, tmp_path: Path) -> None:
    """analyze_drift accepts a valid .yaml file and returns correct results."""
    p = tmp_path / "patterns.yaml"
    p.write_text(_YAML)
    analysis = analyze_drift(graph_db, str(p))
    assert isinstance(analysis, DriftAnalysis)
    assert len(analysis.reports) == 1


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


# ---------------------------------------------------------------------------
# empty / no_signal in analyze_drift (#178)
# ---------------------------------------------------------------------------

_YAML_MISTARGETED = _YAML.replace('fqn_prefix: "cgis.extractors"', 'fqn_prefix: "click.core"')


def test_empty_domain_trips_any_critical(graph_db: str, tmp_path: Path) -> None:
    """A zero-match enforced domain fails the gate despite score 0.0 (#178)."""
    p = tmp_path / "mistargeted.yaml"
    p.write_text(_YAML_MISTARGETED)
    analysis = analyze_drift(graph_db, str(p), max_drift=1.0)
    assert analysis.reports[0].status == "empty"
    assert analysis.any_critical is True


def test_empty_domain_note_suggests_real_prefix(graph_db: str, tmp_path: Path) -> None:
    """The empty note carries closest-prefix suggestions via the suffix index."""
    p = tmp_path / "suggest.yaml"
    p.write_text(_YAML.replace('fqn_prefix: "cgis.extractors"', 'fqn_prefix: "extractors.a"'))
    analysis = analyze_drift(graph_db, str(p), max_drift=1.0)
    report = analysis.reports[0]
    assert report.status == "empty"
    assert report.note is not None
    assert "matched 0 nodes" in report.note
    assert "cgis.extractors.a" in report.note


def test_unenforced_empty_domain_does_not_trip(graph_db: str, tmp_path: Path) -> None:
    """enforce: false keeps observe-only semantics for the new empty term."""
    yaml_text = _YAML_MISTARGETED.replace(
        "drift_tolerance: 0.15", "drift_tolerance: 0.15\n    enforce: false"
    )
    p = tmp_path / "observed.yaml"
    p.write_text(yaml_text)
    analysis = analyze_drift(graph_db, str(p), max_drift=1.0)
    assert analysis.reports[0].status == "empty"
    assert analysis.any_critical is False


def test_no_signal_does_not_trip(tmp_path: Path) -> None:
    """A single isolated node matches → no_signal, gate stays green."""
    db = str(tmp_path / "lone.db")
    lone = Node(
        id="cgis.extractors.lonely",
        type=NodeType.FUNCTION,
        name="lonely",
        file_path="a.py",
        start_line=1,
        end_line=2,
    )
    with SQLiteStore(db) as store:
        store.save_graph([lone], [])
    p = tmp_path / "patterns.yaml"
    p.write_text(_YAML)
    analysis = analyze_drift(db, str(p), max_drift=1.0)
    assert analysis.reports[0].status == "no_signal"
    assert analysis.any_critical is False


def test_profile_filter_excludes_other_profiles(graph_db: str, tmp_path: Path) -> None:
    """profile filter keeps matching + profile-less domains, skips others."""
    yaml_text = (
        _YAML
        + """  - name: "ui"
    fqn_prefix: "components"
    expected_pattern: pure_utility
    profile: typescript
    drift_tolerance: 0.15
  - name: "agnostic"
    fqn_prefix: "cgis.extractors"
    drift_tolerance: 0.99
"""
    )
    p = tmp_path / "multi.yaml"
    p.write_text(yaml_text)
    filtered = analyze_drift(graph_db, str(p), max_drift=1.0, profile="python")
    names = {r.domain for r in filtered.reports}
    assert "ui" not in names  # different explicit profile: excluded
    assert "extraction" in names  # profile None matches any filter
    assert "agnostic" in names  # profile None matches any filter
    unfiltered = analyze_drift(graph_db, str(p), max_drift=1.0)
    assert {r.domain for r in unfiltered.reports} >= {"ui", "extraction", "agnostic"}


def test_observe_only_quotient_empty_does_not_trip_any_critical(tmp_path: Path) -> None:
    """enforce:false PROJECT_LEVEL quotient binding with mis-targeted prefix → empty, gate green.

    Task-3 review gap: mirrors test_quotient_observe_only_does_not_flip_any_critical mechanics
    but targets a mis-matched quotient prefix so status=="empty" instead of a score violation.
    With enforce:false the gate must stay False even when status=="empty".
    """
    # Use a mis-targeted quotient fqn_prefix ("totally.missing") so the quotient
    # graph (collapsed from the three domain nodes) contains nothing at that prefix.
    yaml_text = _YAML_WITH_OBSERVE_ONLY_QUOTIENT.replace(
        'fqn_prefix: "quotient"', 'fqn_prefix: "totally.missing"'
    )
    patterns_file = tmp_path / "patterns.yaml"
    patterns_file.write_text(yaml_text)

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
    edges = [
        Edge(id="e1", source="dom.alpha.x", target="dom.beta.y", type=EdgeType.CALLS),
        Edge(id="e2", source="dom.alpha.x", target="dom.gamma.z", type=EdgeType.CALLS),
        Edge(id="e3", source="dom.beta.y", target="dom.gamma.z", type=EdgeType.CALLS),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)

    analysis = analyze_drift(db, str(patterns_file), max_drift=0.3)

    assert len(analysis.quotient) == 1
    q_binding, q_report = analysis.quotient[0]
    assert q_binding.enforce is False
    assert q_report.status == "empty"
    assert analysis.any_critical is False, (
        "observe-only quotient binding with status='empty' must not flip any_critical"
    )
