"""Unit tests for the shared drift-analysis service."""

from pathlib import Path

import pytest
from conftest import module_with_funcs

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


def test_analyze_drift_any_critical_threshold(graph_db: str, tmp_path: Path) -> None:
    """any_critical respects the default_tolerance passed via max_drift (#170B).

    Spec §2.5 semantic change: the gate is now status-based.  A domain without
    an explicit drift_tolerance uses max_drift as its effective tolerance.
    hub_count=0 violates min:1 → nonzero drift; with a large max_drift the
    domain stays clean/warning; with a very small max_drift the domain is
    critical and any_critical flips.
    """
    # YAML with hub_count min:1 so graph_db (hub_count=0) yields nonzero drift,
    # and NO per-domain drift_tolerance so max_drift becomes the effective tolerance.
    yaml_no_tol = """\
version: "1.0.0"
drift_weights:
  hub_count:        1.0
  star_count:       0.0
  chain_len:        0.0
  dag_depth:        0.0
  router_count:     0.0
  cycle_ratio:      0.0
  unresolved_ratio: 0.0
patterns:
  hub_required:
    description: "Must have hub"
    hub_count: {min: 1}
project_domains:
  - name: "extraction"
    fqn_prefix: "cgis.extractors"
    expected_pattern: hub_required
"""
    p = tmp_path / "notol.yaml"
    p.write_text(yaml_no_tol)
    # Large default_tolerance: score < 1.0 → not critical, gate green.
    lenient = analyze_drift(graph_db, str(p), max_drift=1.0)
    assert lenient.any_critical is False
    # Tiny default_tolerance: score > 0.0 → critical, gate red (old: score>=0.0).
    strict = analyze_drift(graph_db, str(p), max_drift=0.01)
    assert strict.any_critical is True


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


def _triangle_quotient_db(tmp_path: Path) -> str:
    """SQLite db with three single-node domains forming a CALLS triangle (030T at quotient)."""
    db = str(tmp_path / "g.db")
    nodes = [
        Node(
            id=f"dom.{name}.{sym}",
            type=NodeType.FUNCTION,
            name=sym,
            file_path=f"{sym}.py",
            start_line=1,
            end_line=2,
        )
        for name, sym in (("alpha", "x"), ("beta", "y"), ("gamma", "z"))
    ]
    edges = [
        Edge(id="e1", source="dom.alpha.x", target="dom.beta.y", type=EdgeType.CALLS),
        Edge(id="e2", source="dom.alpha.x", target="dom.gamma.z", type=EdgeType.CALLS),
        Edge(id="e3", source="dom.beta.y", target="dom.gamma.z", type=EdgeType.CALLS),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def _mistargeted_quotient_yaml(tmp_path: Path, enforce: bool) -> str:
    """Patterns file whose project_level fqn_prefix matches nothing in the quotient graph."""
    yaml_text = _YAML_WITH_OBSERVE_ONLY_QUOTIENT.replace(
        'fqn_prefix: "quotient"', 'fqn_prefix: "totally.missing"'
    )
    if enforce:
        yaml_text = yaml_text.replace("    enforce: false", "    enforce: true")
    p = tmp_path / "patterns.yaml"
    p.write_text(yaml_text)
    return str(p)


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

    db = _triangle_quotient_db(tmp_path)

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


def test_empty_note_trailing_dot_prefix_no_crash(graph_db: str, tmp_path: Path) -> None:
    """fqn_prefix with a trailing dot yields status 'empty' and 'matched 0 nodes' without crashing.

    Regression guard for the trailing-dot edge case: rsplit('.', 1)[-1] returns an
    empty string when fqn_prefix ends with '.', which must not be passed to
    find_nodes_by_suffix (pointless DB query).  The note must still be well-formed.
    """
    p = tmp_path / "trailing_dot.yaml"
    p.write_text(_YAML.replace('fqn_prefix: "cgis.extractors"', 'fqn_prefix: "ghost."'))
    analysis = analyze_drift(graph_db, str(p), max_drift=1.0)
    report = analysis.reports[0]
    assert report.status == "empty"
    assert report.note is not None
    assert "matched 0 nodes" in report.note


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
    db = _triangle_quotient_db(tmp_path)
    patterns_file = _mistargeted_quotient_yaml(tmp_path, enforce=False)

    analysis = analyze_drift(db, patterns_file, max_drift=0.3)

    assert len(analysis.quotient) == 1
    q_binding, q_report = analysis.quotient[0]
    assert q_binding.enforce is False
    assert q_report.status == "empty"
    # Task-5 review finding: the quotient note must be decorated with "matched 0 nodes"
    # so that a broken fqn_prefix is visible in CLI output (#178 §2.4).
    assert q_report.note is not None, "empty quotient report must carry a diagnostic note"
    assert "matched 0 nodes" in q_report.note
    assert analysis.any_critical is False, (
        "observe-only quotient binding with status='empty' must not flip any_critical"
    )


def test_enforced_quotient_empty_trips_any_critical(tmp_path: Path) -> None:
    """enforce:true PROJECT_LEVEL quotient binding with mis-targeted prefix → empty, gate red.

    Spec §2.3: when a project_level binding has enforce:true (or omits the key,
    defaulting to True) and the quotient graph resolves to status=="empty", the
    gate MUST fire — i.e. analysis.any_critical must be True.  This mirrors the
    mechanics of test_observe_only_quotient_empty_does_not_trip_any_critical but
    flips enforce to True (explicit, because project_level defaults to False) so
    the empty result is treated as a critical violation.
    """
    db = _triangle_quotient_db(tmp_path)
    patterns_file = _mistargeted_quotient_yaml(tmp_path, enforce=True)

    analysis = analyze_drift(db, patterns_file, max_drift=0.3)

    assert len(analysis.quotient) == 1
    q_binding, q_report = analysis.quotient[0]
    assert q_binding.enforce is True
    assert q_report.status == "empty"
    assert analysis.any_critical is True, (
        "enforced quotient binding with status='empty' must trip any_critical"
    )


# ---------------------------------------------------------------------------
# Task-5 acceptance shapes (#176/#170 — httpx wall, owner-api cycle gate,
# per-domain tolerance precedence, quotient routing pin)
# ---------------------------------------------------------------------------

# YAML template shared by the httpx and owner-api acceptance tests: one hygiene
# block with cycle_ratio {max: 0.0} so that any intra-domain cycle trips
# gate_failed, and liberal drift_tolerance so the TV score never interferes.
_YAML_ACCEPTANCE_BASE = """\
version: "1.0.0"
drift_weights:
  hub_count:        0.15
  star_count:       0.15
  chain_len:        0.10
  dag_depth:        0.10
  router_count:     0.10
  cycle_ratio:      0.25
  unresolved_ratio: 0.15
hygiene:
  cycle_ratio:      {max: 0.0}
  unresolved_ratio: {max: 0.2}
"""


def test_httpx_shape_no_cycle_wall(tmp_path: Path) -> None:
    """The httpx scenario: five single-file domains inside a cross-domain import cycle.

    Before #176 every single-file domain in a cross-repo cycle got cycle_ratio=1.0
    and status=gate_failed because the global in_cycle metric had no domain boundary.
    After #176 the intra-domain cycle_ratio is 0.0 for each single-file domain (no
    FILE/MODULE node can form a cycle with itself), so none of the domains gate_fails
    from cycles and any_critical stays False.

    Topology: lib._config → lib._auth → lib._transport → lib._client → lib._utils
               → lib._config (full import cycle).  Each domain has exactly one MODULE
    and two FUNCTION nodes; no intra-domain edges (→ no_signal expected, not clean).
    """
    db = str(tmp_path / "httpx.db")
    prefixes = [
        "lib._config",
        "lib._auth",
        "lib._transport",
        "lib._client",
        "lib._utils",
    ]
    nodes: list[Node] = []
    for p in prefixes:
        nodes.extend(module_with_funcs(p, f"{p.replace('.', '/')}.py", 2))

    # Cross-domain import cycle: each module imports the next, last wraps back.
    edges: list[Edge] = [
        Edge(
            id=f"cyc{i}",
            source=prefixes[i],
            target=prefixes[(i + 1) % len(prefixes)],
            type=EdgeType.IMPORTS,
        )
        for i in range(len(prefixes))
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)

    # Each prefix is its own domain with cycle_ratio {max: 0.0} hygiene.
    domain_entries = "".join(
        f'  - name: "{p}"\n    fqn_prefix: "{p}"\n    drift_tolerance: 0.5\n' for p in prefixes
    )
    yaml_text = _YAML_ACCEPTANCE_BASE + "project_domains:\n" + domain_entries
    patterns_file = str(tmp_path / "httpx.yaml")
    Path(patterns_file).write_text(yaml_text)

    analysis = analyze_drift(db, patterns_file, max_drift=0.5)

    # Each single-file domain has zero intra-domain import edges → cycle_ratio 0.0
    # → no gate_failed from cycles. Status is no_signal (no intra-domain edges).
    for report in analysis.reports:
        assert report.status not in {"gate_failed", "critical"}, (
            f"domain '{report.domain}' must not gate_fail from a cross-domain cycle; "
            f"got status={report.status!r}"
        )
    assert analysis.any_critical is False, (
        "single-file domains inside a cross-domain import cycle must not trip any_critical"
    )


def test_owner_api_shape_cycle_gate_fails(tmp_path: Path) -> None:
    """The owner-api scenario: a multi-file domain with an internal 2-module import cycle.

    With no hygiene_baseline, the intra-domain cycle_ratio = 1.0 (all nodes in cyclic
    files) → violates hygiene {max: 0.0} → status gate_failed → any_critical True.
    After acknowledging the debt via hygiene_baseline, the domain passes and the gate
    is green — proving the ratchet-down mechanic works end-to-end.
    """
    db = str(tmp_path / "owner.db")
    nodes: list[Node] = module_with_funcs("app.svc.a", "app/svc/a.py", 3) + module_with_funcs(
        "app.svc.b", "app/svc/b.py", 3
    )
    edges: list[Edge] = [
        # Intra-domain import cycle between the two modules.
        Edge(id="i1", source="app.svc.a", target="app.svc.b", type=EdgeType.IMPORTS),
        Edge(id="i2", source="app.svc.b", target="app.svc.a", type=EdgeType.IMPORTS),
        # A CALLS edge so edge_count > 0 (avoids no_signal short-circuit).
        Edge(id="c1", source="app.svc.a.f0", target="app.svc.b.f0", type=EdgeType.CALLS),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)

    yaml_bare = (
        _YAML_ACCEPTANCE_BASE
        + "project_domains:\n"
        + '  - name: "svc"\n'
        + '    fqn_prefix: "app.svc"\n'
        + "    drift_tolerance: 0.99\n"
    )
    yaml_baselined = (
        _YAML_ACCEPTANCE_BASE
        + "project_domains:\n"
        + '  - name: "svc"\n'
        + '    fqn_prefix: "app.svc"\n'
        + "    drift_tolerance: 0.99\n"
        + "    hygiene_baseline:\n"
        + "      cycle_ratio: 1.0\n"
    )

    p_bare = str(tmp_path / "bare.yaml")
    p_baselined = str(tmp_path / "baselined.yaml")
    Path(p_bare).write_text(yaml_bare)
    Path(p_baselined).write_text(yaml_baselined)

    # Without baseline: intra-domain cycle → gate_failed.
    analysis_bare = analyze_drift(db, p_bare, max_drift=0.5)
    assert analysis_bare.reports[0].status == "gate_failed", (
        "multi-file domain with internal import cycle must gate_fail without baseline"
    )
    assert analysis_bare.any_critical is True

    # With baseline acknowledging the measured cycle_ratio: gate is green.
    analysis_baselined = analyze_drift(db, p_baselined, max_drift=0.5)
    assert analysis_baselined.reports[0].status != "gate_failed", (
        "intra-domain cycle acknowledged via hygiene_baseline must not gate_fail"
    )
    assert analysis_baselined.any_critical is False


def test_unenforced_gate_failed_does_not_trip(tmp_path: Path) -> None:
    """Unenforced domain with gate_failed status must NOT set any_critical (spec §2.2 observe-only).

    Topology mirrors test_owner_api_shape_cycle_gate_fails: two modules inside one
    domain wired in a 2-module import cycle (plus a CALLS edge to avoid no_signal).
    The intra-domain cycle_ratio = 1.0 → violates hygiene {max: 0.0} → status
    gate_failed.  However, with enforce: false declared on the domain binding the
    gate_failed outcome is observe-only: it must be reported in the DriftReport but
    must NOT contribute to analysis.any_critical.

    Spec §2.2: "observe-only bindings record violations without blocking the gate."
    """
    db = str(tmp_path / "owner.db")
    nodes: list[Node] = module_with_funcs("app.svc.a", "app/svc/a.py", 3) + module_with_funcs(
        "app.svc.b", "app/svc/b.py", 3
    )
    edges: list[Edge] = [
        # Intra-domain import cycle between the two modules.
        Edge(id="i1", source="app.svc.a", target="app.svc.b", type=EdgeType.IMPORTS),
        Edge(id="i2", source="app.svc.b", target="app.svc.a", type=EdgeType.IMPORTS),
        # A CALLS edge so edge_count > 0 (avoids no_signal short-circuit).
        Edge(id="c1", source="app.svc.a.f0", target="app.svc.b.f0", type=EdgeType.CALLS),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)

    yaml_unenforced = (
        _YAML_ACCEPTANCE_BASE
        + "project_domains:\n"
        + '  - name: "svc"\n'
        + '    fqn_prefix: "app.svc"\n'
        + "    drift_tolerance: 0.99\n"
        + "    enforce: false\n"
    )
    p = str(tmp_path / "unenforced.yaml")
    Path(p).write_text(yaml_unenforced)

    analysis = analyze_drift(db, p, max_drift=0.5)

    report = analysis.reports[0]
    assert report.status == "gate_failed", (
        "intra-domain cycle must produce gate_failed even when enforce=false"
    )
    assert analysis.any_critical is False, (
        "unenforced (observe-only) domain with gate_failed must NOT trip any_critical"
    )


def test_per_domain_tolerance_binds_over_default(tmp_path: Path) -> None:
    """The #170B repro: per-domain drift_tolerance takes precedence over max_drift.

    A domain declares drift_tolerance=0.5.  The two-function graph (hub_count=0
    violating {min:1}) yields drift_score≈0.2727, which is above max_drift=0.001
    but below drift_tolerance=0.5.  Two conditions are verified:

    1. With max_drift=0.001 only (no per-domain tolerance): score(0.27) > 0.001
       → critical, any_critical=True.
    2. With the same max_drift=0.001 BUT drift_tolerance=0.5 declared in the
       domain: status != critical, any_critical=False — the per-domain tolerance
       overrides the global default (spec §2.3, #170B).
    """
    db = str(tmp_path / "tol.db")
    nodes: list[Node] = [
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
    edges: list[Edge] = [
        Edge(id="e1", source="cgis.extractors.a", target="cgis.extractors.b", type=EdgeType.CALLS)
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)

    # YAML base: hub_count {min: 1} in pattern; graph produces hub_count=0 → nonzero drift.
    _yaml_base = """\
version: "1.0.0"
drift_weights:
  hub_count:        0.15
  star_count:       0.15
  chain_len:        0.10
  dag_depth:        0.10
  router_count:     0.10
  cycle_ratio:      0.25
  unresolved_ratio: 0.15
hygiene:
  cycle_ratio:      {max: 0.0}
  unresolved_ratio: {max: 0.2}
patterns:
  hub_required:
    description: "Must have hub"
    hub_count: {min: 1}
"""

    # Condition 1: no per-domain tolerance → max_drift=0.001 governs → critical.
    yaml_no_tol = _yaml_base + (
        "project_domains:\n"
        '  - name: "ext"\n'
        '    fqn_prefix: "cgis.extractors"\n'
        "    expected_pattern: hub_required\n"
    )
    p_no_tol = str(tmp_path / "no_tol.yaml")
    Path(p_no_tol).write_text(yaml_no_tol)
    strict = analyze_drift(db, p_no_tol, max_drift=0.001)
    assert strict.any_critical is True, (
        "without per-domain tolerance, score > max_drift=0.001 must trip any_critical"
    )

    # Condition 2: drift_tolerance=0.5 declared → score(≈0.27) < 0.5 → not critical.
    yaml_with_tol = _yaml_base + (
        "project_domains:\n"
        '  - name: "ext"\n'
        '    fqn_prefix: "cgis.extractors"\n'
        "    expected_pattern: hub_required\n"
        "    drift_tolerance: 0.5\n"
    )
    p_with_tol = str(tmp_path / "with_tol.yaml")
    Path(p_with_tol).write_text(yaml_with_tol)
    lenient = analyze_drift(db, p_with_tol, max_drift=0.001)

    report = lenient.reports[0]
    assert report.drift_score > 0.0, "hub=0 must produce nonzero drift"
    assert report.drift_score < 0.5, (
        f"score {report.drift_score:.4f} must be within drift_tolerance=0.5"
    )
    assert report.status != "critical", (
        f"status must not be critical when score={report.drift_score:.4f} < "
        f"drift_tolerance=0.5 (even though score > max_drift=0.001)"
    )
    assert lenient.any_critical is False, (
        "per-domain drift_tolerance=0.5 must override max_drift=0.001 for any_critical"
    )


def test_cross_domain_signal_routes_to_quotient(tmp_path: Path) -> None:
    """Spec §3.1 routing pin: cross-domain cycles appear at the quotient layer, not per-domain.

    Topology: alpha → beta → gamma → alpha CALLS triangle (030T triad).
    Per-domain reports: each domain has a single function with no intra-domain edges
    → no_signal, score 0.0, status never gate_failed.
    Quotient report: pipeline_stage ideal is 021C; the 030T-dominant triangle produces
    a nonzero TV distance → drift_score > 0 at the quotient layer.

    This pins the principle: cross-domain structural signals (030T/030C divergence
    from a chain ideal) are visible in the quotient census/violations, NOT in the
    individual domain reports.  If this test ever fails on the quotient score being
    0.0, it means the routing broke and cross-domain cycles silently vanished.
    """
    db = _triangle_quotient_db(tmp_path)
    patterns_file = tmp_path / "qroute.yaml"
    patterns_file.write_text(_YAML_WITH_OBSERVE_ONLY_QUOTIENT)

    analysis = analyze_drift(db, str(patterns_file), max_drift=0.3)

    # Per-domain: no intra-domain edges → no_signal, no violations, score 0.0.
    for r in analysis.reports:
        assert r.drift_score == pytest.approx(0.0), (
            f"domain '{r.domain}' has no intra-domain edges; score must be 0.0"
        )
        assert r.status not in {"gate_failed", "critical"}, (
            f"cross-domain signal must not leak into per-domain status for '{r.domain}'"
        )

    # Quotient: the 030T triangle vs 021C ideal produces nonzero TV distance.
    assert len(analysis.quotient) == 1, "expected exactly one quotient binding"
    _q_binding, q_report = analysis.quotient[0]
    assert q_report.drift_score > 0.0, (
        "030T-dominant triangle must produce nonzero drift at quotient vs pipeline_stage ideal"
    )
    # The violations name the 030T divergence — cross-domain structural signal is visible.
    assert any("030T" in v for v in q_report.violations), (
        "quotient violations must mention 030T triad to confirm cross-domain signal routing"
    )
    # Domain reports stay clean — the signal is routed to quotient, not leaked down.
    assert analysis.any_critical is False, (
        "observe-only quotient binding must not flip any_critical even with nonzero drift"
    )
