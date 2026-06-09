"""Unit tests for DriftScorer and DriftReport."""

import pytest

from cgis.query.drift import DomainConfig, DriftScorer
from cgis.query.fingerprint import PatternFingerprint

# ── minimal YAML for tests ────────────────────────────────────────────────────

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
    hub_count:        {min: 1}
    star_count:       {exact: 0}
    cycle_ratio:      {max: 0.0}
    unresolved_ratio: {max: 0.1}
  pipeline_stage:
    description: "Chain pattern"
    chain_len:        {min: 2.0}
    cycle_ratio:      {max: 0.0}
project_domains:
  - name: "extraction"
    fqn_prefix: "cgis.extractors"
    expected_pattern: pure_utility
    drift_tolerance: 0.15
  - name: "resolution"
    fqn_prefix: "cgis.resolver"
    expected_pattern: pipeline_stage
    drift_tolerance: 0.20
"""


@pytest.fixture
def scorer(tmp_path: pytest.TempPathFactory) -> DriftScorer:
    """Return a DriftScorer loaded from the minimal YAML fixture."""
    p = tmp_path / "patterns.yaml"  # type: ignore[operator]
    p.write_text(_YAML)
    return DriftScorer(str(p))


@pytest.fixture
def pure_util_domain() -> DomainConfig:
    return DomainConfig(
        name="extraction",
        fqn_prefix="cgis.extractors",
        expected_pattern="pure_utility",
        drift_tolerance=0.15,
    )


# ── load_project_domains ──────────────────────────────────────────────────────


def test_load_project_domains_count(scorer: DriftScorer) -> None:
    """load_project_domains returns one DomainConfig per entry in project_domains."""
    domains = scorer.load_project_domains()
    assert len(domains) == 2


def test_load_project_domains_fields(scorer: DriftScorer) -> None:
    """First domain has the expected field values."""
    domains = scorer.load_project_domains()
    d = domains[0]
    assert d.name == "extraction"
    assert d.fqn_prefix == "cgis.extractors"
    assert d.expected_pattern == "pure_utility"
    assert d.drift_tolerance == pytest.approx(0.15)


# ── drift = 0 when perfectly matching ────────────────────────────────────────


def test_drift_zero_when_actual_matches_ideal(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """hub_count=1, star_count=0, cycle_ratio=0.0, unresolved_ratio=0.0 → drift=0, status=clean."""
    perfect = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(perfect, pure_util_domain)
    assert report.drift_score == pytest.approx(0.0)
    assert report.status == "clean"
    assert report.violations == []


# ── satisfying min constraint doesn't create drift ───────────────────────────


def test_exceeding_min_does_not_penalise(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """hub_count=5 satisfies min:1 — drift contribution from hub_count must be 0."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=5,  # well above min=1, no violation
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.drift_score == pytest.approx(0.0)


# ── violations list ───────────────────────────────────────────────────────────


def test_violations_list_when_below_min(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """hub_count=0 violates min:1 → violation string includes 'hub_count'."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert any("hub_count" in v for v in report.violations)


def test_violations_list_when_above_max(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """cycle_ratio=0.5 violates max:0.0 → violation string includes 'cycle_ratio'."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.5,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert any("cycle_ratio" in v for v in report.violations)


def test_violations_list_when_exact_mismatch(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """star_count=2 violates exact:0 → violation string includes 'star_count'."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=2,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert any("star_count" in v for v in report.violations)


# ── status thresholds ─────────────────────────────────────────────────────────


def test_status_critical_for_god_object(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """All constrained components violated → status=critical."""
    god_object = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=0,  # violates min:1
        star_count=5,  # violates exact:0
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.8,  # violates max:0.0
        unresolved_ratio=0.9,  # violates max:0.1
    )
    report = scorer.score(god_object, pure_util_domain)
    assert report.status == "critical"
    assert report.drift_score >= 0.50


def test_status_clean_below_threshold(scorer: DriftScorer, pure_util_domain: DomainConfig) -> None:
    """A slightly imperfect fingerprint below 0.20 → status=clean."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.05,  # below max:0.1, no violation, no drift
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.status == "clean"


# ── report fields ──────────────────────────────────────────────────────────────


def test_report_contains_domain_metadata(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """DriftReport carries domain name, fqn_prefix, expected_pattern, tolerance."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.domain == "extraction"
    assert report.fqn_prefix == "cgis.extractors"
    assert report.expected_pattern == "pure_utility"
    assert report.tolerance == pytest.approx(0.15)


# ── unconstrained components ──────────────────────────────────────────────────


def test_unconstrained_components_add_no_drift(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """chain_len and dag_depth are unconstrained in pure_utility — any value → zero drift."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=1,
        star_count=0,
        chain_len=100.0,  # unconstrained — must not add drift
        dag_depth=50,  # unconstrained — must not add drift
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.drift_score == pytest.approx(0.0)
