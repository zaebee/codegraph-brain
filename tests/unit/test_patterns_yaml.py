"""Structural validation for docs/ontology/patterns.yaml (unified alphabet, spec §2)."""

from pathlib import Path

import yaml

PATTERNS_PATH = Path(__file__).parent.parent.parent / "docs" / "ontology" / "patterns.yaml"
PATTERNS_UI_PATH = PATTERNS_PATH.parent / "patterns_ui.yaml"

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

_ALPHABET = frozenset(
    {"pure_utility", "pipeline_stage", "orchestrator", "layered_dag", "dispatcher"}
)


def _load() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(PATTERNS_PATH.read_text())


def _constraint_items(template: dict) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    """Return (component, constraint-dict) pairs, skipping description/params keys."""
    return [
        (key, value)
        for key, value in template.items()
        if key not in ("description", "params") and isinstance(value, dict)
    ]


def test_patterns_yaml_exists() -> None:
    """The patterns.yaml file must exist at docs/ontology/patterns.yaml."""
    assert PATTERNS_PATH.exists()


def test_patterns_ui_yaml_deleted() -> None:
    """The forked UI ontology must not exist — the alphabet is single (spec §2.4)."""
    assert not PATTERNS_UI_PATH.exists()


def test_required_top_level_keys() -> None:
    """Top-level keys: version, profiles, hygiene, patterns, project_domains."""
    data = _load()
    for key in ("version", "profiles", "hygiene", "patterns", "project_domains"):
        assert key in data, f"Missing top-level key '{key}'"


def test_alphabet_is_closed() -> None:
    """Exactly the five templates of the closed alphabet — no more, no less."""
    data = _load()
    assert set(data["patterns"].keys()) == _ALPHABET


def test_each_profile_weights_cover_all_components_and_sum_to_one() -> None:
    """Every profile's drift_weights has exactly the 7 components, summing to 1.0."""
    data = _load()
    assert set(data["profiles"].keys()) >= {"python", "typescript"}
    for name, profile in data["profiles"].items():
        weights = profile["drift_weights"]
        assert set(weights.keys()) == _COMPONENT_NAMES, f"profile '{name}'"
        assert abs(sum(weights.values()) - 1.0) < 1e-9, f"profile '{name}'"


def test_hygiene_uses_known_components() -> None:
    """The hygiene block constrains only known components."""
    data = _load()
    assert set(data["hygiene"].keys()) <= _COMPONENT_NAMES


def test_all_pattern_constraints_use_known_components() -> None:
    """No template references an unknown component (params/description excluded)."""
    data = _load()
    for pattern_name, template in data["patterns"].items():
        for key in template:
            if key in ("description", "params"):
                continue
            assert key in _COMPONENT_NAMES, f"Unknown component '{key}' in '{pattern_name}'"


def test_each_constraint_has_exactly_one_operator() -> None:
    """Each constraint (templates and hygiene) has exactly one of min/max/exact."""
    data = _load()
    blocks = [*data["patterns"].values(), data["hygiene"]]
    for template in blocks:
        for key, value in _constraint_items(template):
            ops = set(value.keys()) & {"min", "max", "exact"}
            assert len(ops) == 1, f"Constraint '{key}' needs exactly one operator, got {ops}"


def test_placeholders_resolve_to_declared_params() -> None:
    """Every $name in a template resolves to that template's params block."""
    data = _load()
    undeclared = [
        f"'{op_value}' in '{pattern_name}.{key}' is undeclared"
        for pattern_name, template in data["patterns"].items()
        for key, value in _constraint_items(template)
        for op_value in value.values()
        if isinstance(op_value, str)
        and op_value.startswith("$")
        and op_value[1:] not in set((template.get("params") or {}).keys())
    ]
    assert not undeclared, "\n".join(undeclared)


def test_project_domains_have_required_fields() -> None:
    """Each domain has name, fqn_prefix, profile, drift_tolerance (expected_pattern optional)."""
    data = _load()
    for domain in data["project_domains"]:
        for key in ("name", "fqn_prefix", "profile", "drift_tolerance"):
            assert key in domain, f"Domain '{domain.get('name', '?')}' missing '{key}'"


def test_project_domains_reference_known_patterns_and_profiles() -> None:
    """expected_pattern (when present) and profile must reference declared entries."""
    data = _load()
    known_patterns = set(data["patterns"].keys())
    known_profiles = set(data["profiles"].keys())
    for domain in data["project_domains"]:
        if "expected_pattern" in domain:
            assert domain["expected_pattern"] in known_patterns, domain["name"]
        assert domain["profile"] in known_profiles, domain["name"]


def test_domain_params_override_only_declared_params() -> None:
    """Domain params keys must be declared in the bound template's params block."""
    data = _load()
    for domain in data["project_domains"]:
        if "params" not in domain or "expected_pattern" not in domain:
            continue
        template = data["patterns"][domain["expected_pattern"]]
        declared = set((template.get("params") or {}).keys())
        assert set(domain["params"].keys()) <= declared, domain["name"]
