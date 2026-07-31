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

# Hygiene may constrain the 7 v1 components plus tangle_ratio — a v2 census-derived
# hard gate that is NOT a drift_weights component (#186).
_HYGIENE_COMPONENTS = _COMPONENT_NAMES | {"tangle_ratio"}

# funnel = transpose(layered_dag); added per #186 research (the single most common
# intra-domain archetype across 9 repos) so fit-quality (#177) measures against a
# transpose-closed alphabet instead of flagging its absence as "no template fits".
_ALPHABET = frozenset(
    {"pure_utility", "pipeline_stage", "orchestrator", "layered_dag", "dispatcher", "funnel"}
)

_TRIAD_ORDER = frozenset(
    {
        "021D",
        "021U",
        "021C",
        "111D",
        "111U",
        "030T",
        "030C",
        "201",
        "120D",
        "120U",
        "120C",
        "210",
        "300",
    }
)

_COUNTING_COMPONENTS = frozenset({"hub_count", "star_count", "chain_len", "router_count"})


def _load() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(PATTERNS_PATH.read_text())


def _constraint_items(template: dict) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    """Return (component, constraint-dict) pairs, skipping description/params/ideal keys."""
    return [
        (key, value)
        for key, value in template.items()
        if key not in ("description", "params", "ideal") and isinstance(value, dict)
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
    assert set(data["hygiene"].keys()) <= _HYGIENE_COMPONENTS


def test_all_pattern_constraints_use_known_components() -> None:
    """No template references an unknown component (params/description/ideal excluded)."""
    data = _load()
    for pattern_name, template in data["patterns"].items():
        for key in template:
            if key in ("description", "params", "ideal"):
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


def test_every_template_has_ideal_with_both_layers() -> None:
    """Each of the 5 templates declares ideal.imports and ideal.calls summing to 1."""
    data = _load()
    for name, template in data["patterns"].items():
        ideal = template.get("ideal")
        assert isinstance(ideal, dict), name
        assert set(ideal) == {"imports", "calls"}, name
        for layer, point in ideal.items():
            assert set(point) <= _TRIAD_ORDER, f"{name}.{layer}"
            assert abs(sum(point.values()) - 1.0) < 1e-9, f"{name}.{layer}"


def test_templates_carry_no_counting_constraints() -> None:
    """Counting components moved to the distance term — gates only (spec §3.3)."""
    data = _load()
    for name, template in data["patterns"].items():
        for key, _ in _constraint_items(template):
            msg = f"'{key}' in '{name}' must be an ideal, not a gate"
            assert key not in _COUNTING_COMPONENTS, msg


def test_profiles_declare_layers_summing_to_one() -> None:
    """Both profiles declare imports/calls/gates layer weights summing to 1."""
    data = _load()
    for name, profile in data["profiles"].items():
        layers = profile["layers"]
        assert set(layers) == {"imports", "calls", "gates"}, name
        assert abs(sum(layers.values()) - 1.0) < 1e-9, name


def test_project_level_binding_observe_only() -> None:
    """project_level exists, matches the quotient prefix, and is enforce: false."""
    data = _load()
    bindings = data["project_level"]
    assert len(bindings) == 1
    b = bindings[0]
    assert b["fqn_prefix"] == "quotient"
    assert b["enforce"] is False
    assert b["expected_pattern"] in data["patterns"]


# ---------------------------------------------------------------------------
# Drift-tolerance ratchet (#151)
# ---------------------------------------------------------------------------

TOLERANCES_LOCK_PATH = PATTERNS_PATH.parent / "tolerances.lock"

_TOLERANCE_SECTIONS = ("project_domains", "project_level")


def _current_tolerances() -> dict[str, dict[str, float]]:
    """Return {section: {domain_name: drift_tolerance}} as declared in patterns.yaml."""
    config = _load()
    return {
        section: {
            entry["name"]: float(entry["drift_tolerance"])
            for entry in (config.get(section) or [])
            if "drift_tolerance" in entry
        }
        for section in _TOLERANCE_SECTIONS
    }


def _locked_tolerances() -> dict[str, dict[str, float]]:
    """Return {section: {domain_name: locked_tolerance}} from tolerances.lock."""
    locked = yaml.safe_load(TOLERANCES_LOCK_PATH.read_text()) or {}
    return {
        section: {name: float(value) for name, value in (locked.get(section) or {}).items()}
        for section in _TOLERANCE_SECTIONS
    }


def test_tolerances_lock_exists() -> None:
    """The ratchet lock must be committed alongside patterns.yaml."""
    assert TOLERANCES_LOCK_PATH.exists(), f"missing ratchet lock: {TOLERANCES_LOCK_PATH}"


def test_tolerances_lock_covers_exactly_the_declared_domains() -> None:
    """Lock keys and patterns.yaml keys must match exactly, in both directions.

    A domain missing from the lock would dodge the ratchet entirely — including
    via rename, which otherwise reads as "old domain deleted, new domain added".
    A stale lock entry is dead weight that hides which bound is still live.
    """
    current, locked = _current_tolerances(), _locked_tolerances()
    for section in _TOLERANCE_SECTIONS:
        unlocked = sorted(set(current[section]) - set(locked[section]))
        assert not unlocked, (
            f"{section}: {unlocked} declare a drift_tolerance but are absent from "
            f"{TOLERANCES_LOCK_PATH.name} — add them at their current value"
        )
        stale = sorted(set(locked[section]) - set(current[section]))
        assert not stale, (
            f"{section}: {stale} are locked but no longer declared in patterns.yaml — "
            f"remove them from {TOLERANCES_LOCK_PATH.name}"
        )


def test_tolerances_never_exceed_the_lock() -> None:
    """drift_tolerance is a ratchet: values may only go DOWN (#140/#141, #151).

    Raising a tolerance masks a regression. Lowering one is the whole point, and
    requires updating tolerances.lock in the same PR so the change lands in the
    diff where a reviewer sees it.
    """
    current, locked = _current_tolerances(), _locked_tolerances()
    raised = [
        f"{section}.{name}: {value:.2f} > locked {locked[section][name]:.2f}"
        for section in _TOLERANCE_SECTIONS
        for name, value in current[section].items()
        if name in locked[section] and value > locked[section][name] + 1e-9
    ]
    assert not raised, (
        "drift_tolerance may only ratchet DOWN; these were raised:\n  "
        + "\n  ".join(raised)
        + f"\nIf this is a deliberate re-baseline, update {TOLERANCES_LOCK_PATH.name} "
        "and justify it in the PR description."
    )
