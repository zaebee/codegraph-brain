"""Unit tests for DriftScorer and DriftReport."""

from pathlib import Path

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
def scorer(tmp_path: Path) -> DriftScorer:
    """Return a DriftScorer loaded from the minimal YAML fixture."""
    p = tmp_path / "patterns.yaml"
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


# ── extended DomainConfig loading (spec §2.2/§2.3 bindings) ───────────────────

_YAML_EXTENDED = """\
version: "2.0.0"
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
patterns:
  layered_dag:
    description: "Layered imports"
    params:
      min_depth: 3
    dag_depth:        {min: $min_depth}
    cycle_ratio:      {max: 0.0}
project_domains:
  - name: "components"
    fqn_prefix: "components"
    expected_pattern: layered_dag
    profile: python
    params: {min_depth: 2}
    drift_tolerance: 0.15
  - name: "hooks"
    fqn_prefix: "hooks"
    drift_tolerance: 0.15
"""


@pytest.fixture
def extended_scorer(tmp_path: Path) -> DriftScorer:
    """Return a DriftScorer loaded from the extended (v2) YAML fixture."""
    p = tmp_path / "patterns.yaml"
    p.write_text(_YAML_EXTENDED)
    return DriftScorer(str(p))


def test_domain_config_loads_profile_and_params(extended_scorer: DriftScorer) -> None:
    """profile and params are read from the domain binding."""
    domains = extended_scorer.load_project_domains()
    d = domains[0]
    assert d.profile == "python"
    assert d.params == {"min_depth": 2.0}


def test_domain_config_defaults_for_legacy_yaml(scorer: DriftScorer) -> None:
    """Bindings without profile/params load with profile=None, params={}."""
    d = scorer.load_project_domains()[0]
    assert d.profile is None
    assert d.params == {}


def test_domain_config_expected_pattern_optional(extended_scorer: DriftScorer) -> None:
    """A binding without expected_pattern loads with expected_pattern=None."""
    hooks = extended_scorer.load_project_domains()[1]
    assert hooks.expected_pattern is None


# ── param value validation ────────────────────────────────────────────────────


def test_load_params_rejects_null_values(tmp_path: Path) -> None:
    """A YAML entry with params: {min_depth: } (null) raises TypeError with domain name."""
    yaml_with_null_param = _YAML_EXTENDED.replace("params: {min_depth: 2}", "params: {min_depth: }")
    p = tmp_path / "patterns.yaml"
    p.write_text(yaml_with_null_param)
    scorer = DriftScorer(str(p))
    with pytest.raises(TypeError, match="min_depth"):
        scorer.load_project_domains()


# ── §2.3 measurement profiles ─────────────────────────────────────────────────

_YAML_PROFILES = """\
version: "2.0.0"
profiles:
  python:
    drift_weights:
      hub_count:        0.40
      star_count:       0.20
      chain_len:        0.10
      dag_depth:        0.10
      router_count:     0.05
      cycle_ratio:      0.10
      unresolved_ratio: 0.05
patterns:
  pure_utility:
    description: "Hub"
    hub_count:   {min: 1}
    star_count:  {exact: 0}
project_domains:
  - name: "lib"
    fqn_prefix: "lib"
    expected_pattern: pure_utility
    profile: python
    drift_tolerance: 0.15
"""


def _profile_fp(hub: int, star: int) -> PatternFingerprint:
    """Fingerprint helper for profile-weight tests."""
    return PatternFingerprint(
        domain="lib",
        hub_count=hub,
        star_count=star,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )


def test_profile_weights_used_when_domain_names_profile(tmp_path: Path) -> None:
    """A domain naming a profile is scored with that profile's drift_weights."""
    p = tmp_path / "patterns.yaml"
    p.write_text(_YAML_PROFILES)
    scorer = DriftScorer(str(p))
    domain = scorer.load_project_domains()[0]
    # hub violated, star clean. Profile weights hub .40 / star .20 → drift = .4/.6 = 2/3.
    # (Asymmetric weights on purpose: the equal-split fallback would give 0.5,
    # so 2/3 proves the profile weights were actually used.)
    report = scorer.score(_profile_fp(hub=0, star=0), domain)
    assert report.drift_score == pytest.approx(2 / 3, abs=1e-6)


def test_unknown_profile_raises(tmp_path: Path) -> None:
    """A domain naming an undeclared profile is a config error."""
    yaml_text = _YAML_PROFILES.replace("profile: python", "profile: golang")
    p = tmp_path / "patterns.yaml"
    p.write_text(yaml_text)
    scorer = DriftScorer(str(p))
    domain = scorer.load_project_domains()[0]
    with pytest.raises(ValueError, match="golang"):
        scorer.score(_profile_fp(hub=1, star=0), domain)


def test_top_level_weights_remain_default(
    scorer: DriftScorer, pure_util_domain: DomainConfig
) -> None:
    """Domains without a profile keep using top-level drift_weights (legacy layout)."""
    fp = PatternFingerprint(
        domain="cgis.extractors",
        hub_count=0,  # violates min:1 → nonzero drift proves weights were found
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    report = scorer.score(fp, pure_util_domain)
    assert report.drift_score > 0.0


# ── §2.2 parameterized templates ──────────────────────────────────────────────


def _fp_with_dag(depth: int) -> PatternFingerprint:
    """Fingerprint with only dag_depth set — helper for param tests."""
    return PatternFingerprint(
        domain="components",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=depth,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )


def test_domain_params_override_template_default(extended_scorer: DriftScorer) -> None:
    """components overrides min_depth to 2 — dag_depth=2 is clean."""
    domain = extended_scorer.load_project_domains()[0]
    report = extended_scorer.score(_fp_with_dag(2), domain)
    assert report.drift_score == pytest.approx(0.0)
    assert report.violations == []


def test_template_param_default_applies_without_override(tmp_path: Path) -> None:
    """Without a domain override, the template default min_depth=3 governs."""
    yaml_text = _YAML_EXTENDED.replace("    params: {min_depth: 2}\n", "")
    assert yaml_text != _YAML_EXTENDED, "replace failed: domain params override line not found"
    p = tmp_path / "patterns.yaml"
    p.write_text(yaml_text)
    scorer = DriftScorer(str(p))
    domain = scorer.load_project_domains()[0]
    report = scorer.score(_fp_with_dag(2), domain)
    assert any("dag_depth" in v for v in report.violations)  # 2 < min 3


def test_unknown_param_key_raises(tmp_path: Path) -> None:
    """Overriding a parameter the template never declared is a config error."""
    yaml_text = _YAML_EXTENDED.replace("params: {min_depth: 2}", "params: {max_fanout: 5}")
    assert yaml_text != _YAML_EXTENDED, "replace failed: domain params override line not found"
    p = tmp_path / "patterns.yaml"
    p.write_text(yaml_text)
    scorer = DriftScorer(str(p))
    domain = scorer.load_project_domains()[0]
    with pytest.raises(ValueError, match="max_fanout"):
        scorer.score(_fp_with_dag(2), domain)


def test_unresolvable_placeholder_raises(tmp_path: Path) -> None:
    """A $placeholder with no declared parameter is a config error."""
    yaml_text = _YAML_EXTENDED.replace("    params:\n      min_depth: 3\n", "")
    assert yaml_text != _YAML_EXTENDED, "replace failed: template params block not found"
    yaml_no_override = yaml_text.replace("    params: {min_depth: 2}\n", "")
    assert yaml_no_override != yaml_text, "replace failed: domain params override line not found"
    p = tmp_path / "patterns.yaml"
    p.write_text(yaml_no_override)
    scorer = DriftScorer(str(p))
    domain = scorer.load_project_domains()[0]
    with pytest.raises(ValueError, match="min_depth"):
        scorer.score(_fp_with_dag(2), domain)


# ── §2.1 global hygiene invariants ────────────────────────────────────────────

_YAML_HYGIENE = """\
version: "2.0.0"
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
  pure_utility:
    description: "Hub"
    hub_count:        {min: 1}
    unresolved_ratio: {max: 0.1}
project_domains:
  - name: "hygiene_only"
    fqn_prefix: "hooks"
    drift_tolerance: 0.15
  - name: "templated"
    fqn_prefix: "lib"
    expected_pattern: pure_utility
    drift_tolerance: 0.15
"""


@pytest.fixture
def hygiene_scorer(tmp_path: Path) -> DriftScorer:
    """Return a DriftScorer with a global hygiene block."""
    p = tmp_path / "patterns.yaml"
    p.write_text(_YAML_HYGIENE)
    return DriftScorer(str(p))


def _hygiene_fp(cycle: float = 0.0, unresolved: float = 0.0, hub: int = 1) -> PatternFingerprint:
    """Fingerprint helper for hygiene tests."""
    return PatternFingerprint(
        domain="hooks",
        hub_count=hub,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=cycle,
        unresolved_ratio=unresolved,
    )


def test_hygiene_only_domain_clean_when_hygienic(hygiene_scorer: DriftScorer) -> None:
    """No expected_pattern + clean cycles/resolution → zero drift."""
    domain = hygiene_scorer.load_project_domains()[0]
    report = hygiene_scorer.score(_hygiene_fp(), domain)
    assert report.drift_score == pytest.approx(0.0)
    assert report.expected_pattern is None


def test_hygiene_only_domain_flags_cycles(hygiene_scorer: DriftScorer) -> None:
    """A cycle in a hygiene-only domain violates the global invariant."""
    domain = hygiene_scorer.load_project_domains()[0]
    report = hygiene_scorer.score(_hygiene_fp(cycle=0.5), domain)
    assert report.drift_score > 0.0
    assert any("cycle_ratio" in v for v in report.violations)


def test_template_constraint_wins_over_hygiene(hygiene_scorer: DriftScorer) -> None:
    """pure_utility's unresolved max:0.1 overrides hygiene's max:0.2."""
    domain = hygiene_scorer.load_project_domains()[1]
    report = hygiene_scorer.score(_hygiene_fp(unresolved=0.15), domain)
    # 0.15 is legal under hygiene (0.2) but violates the template (0.1)
    assert any("unresolved_ratio" in v for v in report.violations)


def test_hygiene_applies_to_templated_domain(hygiene_scorer: DriftScorer) -> None:
    """pure_utility has no cycle constraint of its own — hygiene's max:0.0 still applies."""
    domain = hygiene_scorer.load_project_domains()[1]
    report = hygiene_scorer.score(_hygiene_fp(cycle=0.3), domain)
    assert any("cycle_ratio" in v for v in report.violations)


# ── §2.3 confidence discount ──────────────────────────────────────────────────

_YAML_DISCOUNT = """\
version: "2.0.0"
drift_weights:
  hub_count:        0.25
  star_count:       0.25
  chain_len:        0.0
  dag_depth:        0.25
  router_count:     0.0
  cycle_ratio:      0.25
  unresolved_ratio: 0.0
patterns:
  mixed:
    description: "CALLS + IMPORTS constraints"
    hub_count:   {min: 2}
    star_count:  {exact: 0}
    dag_depth:   {min: 2}
    cycle_ratio: {max: 0.0}
project_domains:
  - name: "m"
    fqn_prefix: "m"
    expected_pattern: mixed
    drift_tolerance: 0.5
"""


@pytest.fixture
def discount_scorer(tmp_path: Path) -> DriftScorer:
    """Return a DriftScorer for confidence-discount tests."""
    p = tmp_path / "patterns.yaml"
    p.write_text(_YAML_DISCOUNT)
    return DriftScorer(str(p))


def _violating_fp(unresolved: float) -> PatternFingerprint:
    """All four constraints violated; only unresolved_ratio varies."""
    return PatternFingerprint(
        domain="m",
        hub_count=0,  # violates min:2
        star_count=3,  # violates exact:0
        chain_len=0.0,
        dag_depth=0,  # violates min:2
        router_count=0,
        cycle_ratio=1.0,  # violates max:0.0
        unresolved_ratio=unresolved,
    )


def test_full_unresolved_zeroes_calls_layer(discount_scorer: DriftScorer) -> None:
    """At unresolved=1.0 CALLS components carry zero weight; clean IMPORTS → zero drift."""
    domain = discount_scorer.load_project_domains()[0]
    fp_imports_clean = PatternFingerprint(
        domain="m",
        hub_count=0,  # violates min:2 (CALLS) — but fully discounted
        star_count=3,  # violates exact:0 (CALLS) — but fully discounted
        chain_len=0.0,
        dag_depth=2,  # satisfies (IMPORTS)
        router_count=0,
        cycle_ratio=0.0,  # satisfies (IMPORTS)
        unresolved_ratio=1.0,
    )
    report = discount_scorer.score(fp_imports_clean, domain)
    assert report.drift_score == pytest.approx(0.0)
    # Violations are still REPORTED for discounted components — only the
    # score honors the discount (plan: Out of Scope / violation suppression).
    assert any("hub_count" in v for v in report.violations)
    assert any("star_count" in v for v in report.violations)


def test_discount_scales_calls_contribution(discount_scorer: DriftScorer) -> None:
    """All components violated: discount renormalizes but everything is drift 1.0.

    Pins the invariant: the discount changes *relative weights*; it must not
    deflate a uniformly-violated domain below 1.0.
    """
    domain = discount_scorer.load_project_domains()[0]
    assert discount_scorer.score(_violating_fp(0.0), domain).drift_score == pytest.approx(1.0)
    assert discount_scorer.score(_violating_fp(0.6), domain).drift_score == pytest.approx(1.0)


def test_discount_scaling_visible_when_only_calls_violated(
    discount_scorer: DriftScorer,
) -> None:
    """With only CALLS components violated, drift falls as unresolved rises."""
    domain = discount_scorer.load_project_domains()[0]

    def fp(unresolved: float) -> PatternFingerprint:
        return PatternFingerprint(
            domain="m",
            hub_count=0,  # violated (CALLS)
            star_count=3,  # violated (CALLS)
            chain_len=0.0,
            dag_depth=2,  # clean (IMPORTS)
            router_count=0,
            cycle_ratio=0.0,  # clean (IMPORTS)
            unresolved_ratio=unresolved,
        )

    d0 = discount_scorer.score(fp(0.0), domain).drift_score
    d6 = discount_scorer.score(fp(0.6), domain).drift_score
    # u=0.0: eff = (.25,.25,.25,.25), violated share = 0.5 → drift 0.5
    # u=0.6: eff = (.1,.1,.25,.25) tot .7, violated share = .2/.7 ≈ 0.2857
    assert d0 == pytest.approx(0.5)
    assert d6 == pytest.approx(0.2 / 0.7, abs=1e-6)
