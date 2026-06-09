"""Structural validation for docs/ontology/patterns.yaml."""

from pathlib import Path

import yaml

PATTERNS_PATH = Path(__file__).parent.parent.parent / "docs" / "ontology" / "patterns.yaml"

_COMPONENT_NAMES = frozenset(
    {
        "hub_count",
        "star_count",
        "chain_len",
        "dag_depth",
        "router_count",
        "cycle_ratio",
        "unresolved_ratio",
    }
)


def _load() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(PATTERNS_PATH.read_text())


def test_patterns_yaml_exists() -> None:
    """The patterns.yaml file must exist at docs/ontology/patterns.yaml."""
    assert PATTERNS_PATH.exists()


def test_required_top_level_keys() -> None:
    """Top-level keys: version, drift_weights, patterns, project_domains."""
    data = _load()
    assert "version" in data
    assert "drift_weights" in data
    assert "patterns" in data
    assert "project_domains" in data


def test_drift_weights_cover_exactly_all_components() -> None:
    """drift_weights must have exactly the 7 component names."""
    data = _load()
    assert set(data["drift_weights"].keys()) == _COMPONENT_NAMES


def test_drift_weights_sum_to_one() -> None:
    """Weights must sum to 1.0 (within floating-point tolerance)."""
    data = _load()
    total = sum(data["drift_weights"].values())
    assert abs(total - 1.0) < 1e-9


def test_all_pattern_constraints_use_known_components() -> None:
    """No pattern template may reference an unknown component name."""
    data = _load()
    for pattern_name, template in data["patterns"].items():
        for key in template:
            if key == "description":
                continue
            assert key in _COMPONENT_NAMES, f"Unknown component '{key}' in pattern '{pattern_name}'"


def test_each_constraint_has_exactly_one_operator() -> None:
    """Each component constraint must have exactly one of: min, max, exact."""
    data = _load()
    for pattern_name, template in data["patterns"].items():
        for key, value in template.items():
            if key == "description" or not isinstance(value, dict):
                continue
            ops = set(value.keys()) & {"min", "max", "exact"}
            assert len(ops) == 1, (
                f"Constraint '{key}' in '{pattern_name}' must have exactly one operator, got {ops}"
            )


def test_project_domains_have_required_fields() -> None:
    """Each project domain must have name, fqn_prefix, expected_pattern, drift_tolerance."""
    data = _load()
    for domain in data["project_domains"]:
        assert "name" in domain
        assert "fqn_prefix" in domain
        assert "expected_pattern" in domain
        assert "drift_tolerance" in domain


def test_project_domains_reference_known_patterns() -> None:
    """expected_pattern in each domain must refer to a defined pattern."""
    data = _load()
    known = set(data["patterns"].keys())
    for domain in data["project_domains"]:
        assert domain["expected_pattern"] in known, (
            f"Domain '{domain['name']}' references unknown pattern '{domain['expected_pattern']}'"
        )
