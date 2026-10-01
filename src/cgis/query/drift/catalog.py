"""PatternCatalog: patterns.yaml loaded and resolved, for DriftScorer to score against (#214).

Everything here answers "what does the ontology say": domain bindings, a
template's ideal triads, a profile's layer and triad weights, the constraints a
template declares with its `$params` substituted. Nothing here looks at a
fingerprint — that is `scoring.py` — which is what lets `ontology_init` (#174)
and the scorer share one loaded ontology.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from cgis.query.drift.triads import TRIAD_ORDER

#: The fingerprint components a template or the hygiene block may constrain.
COMPONENT_NAMES = (
    "hub_count",
    "star_count",
    "chain_len",
    "dag_depth",
    "router_count",
    "cycle_ratio",
    "unresolved_ratio",
    "tangle_ratio",
)


@dataclass(frozen=True)
class DomainConfig:
    """Project-level domain expectation loaded from patterns.yaml."""

    name: str
    fqn_prefix: str
    expected_pattern: str | None
    drift_tolerance: float | None = None
    profile: str | None = None
    params: dict[str, float] = field(default_factory=dict)
    # Acknowledged hygiene debt: per-constraint relaxed bound (ratchet-down
    # convention; values may only decrease over time). Spec §2.2.
    hygiene_baseline: dict[str, float] = field(default_factory=dict)
    # When False the domain is audited but violations never block CI.
    enforce: bool = True


def _validate_mapping(node: object, owner: str) -> dict[str, Any]:
    """Return node unchanged after validating it is a mapping.

    Raises TypeError otherwise (e.g. a YAML block hand-edited into a list).
    """
    if not isinstance(node, dict):
        msg = f"{owner} must be a mapping, got {type(node).__name__}."
        raise TypeError(msg)
    return node


def _params_mapping(container: dict[str, Any], owner: str) -> dict[str, Any]:
    """Return the container's params block, validating it is a mapping.

    Raises TypeError if params is present but not a mapping (e.g. a list).
    """
    return _validate_mapping(container.get("params") or {}, f"{owner} params")


def _load_params(d: dict[str, Any]) -> dict[str, float]:
    """Parse a domain binding's params block; non-numeric values fail loud.

    Raises TypeError if any param value is not numeric (int, float, or bool).
    """
    params: dict[str, float] = {}
    for k, v in _params_mapping(d, f"Domain '{d.get('name', '?')}'").items():
        if not isinstance(v, (int, float)):
            msg = f"Domain '{d.get('name', '?')}' param '{k}' must be numeric, got {v!r}."
            raise TypeError(msg)
        params[k] = float(v)
    return params


def _parse_hygiene_baseline(d: dict[str, Any], hygiene_keys: set[str]) -> dict[str, float]:
    """Parse and validate the hygiene_baseline block from a domain binding dict.

    Raises ValueError if any baseline key is not a valid hygiene constraint key,
    and TypeError if a value is not numeric (int, float, or bool).
    """
    raw = d.get("hygiene_baseline")
    if raw is None:
        return {}
    mapping = _validate_mapping(raw, f"Domain '{d.get('name', '?')}' hygiene_baseline")
    result: dict[str, float] = {}
    for key, val in mapping.items():
        if key not in hygiene_keys:
            valid = sorted(hygiene_keys)
            msg = (
                f"hygiene_baseline key '{key}' in domain '{d.get('name', '?')}' "
                f"names no hygiene constraint; valid keys: {valid}"
            )
            raise ValueError(msg)
        if not isinstance(val, (int, float)):
            msg = (
                f"Domain '{d.get('name', '?')}' hygiene_baseline key '{key}' "
                f"must be numeric, got {val!r}."
            )
            raise TypeError(msg)
        result[key] = float(val)
    return result


def _build_domain_config(
    d: dict[str, Any], hygiene_keys: set[str], *, enforce_default: bool
) -> DomainConfig:
    """Build one DomainConfig from a YAML binding dict."""
    raw_tol = d.get("drift_tolerance")
    # Baseline before params: with both malformed, the baseline error is the one
    # raised, as it was before the split (#214).
    hygiene_baseline = _parse_hygiene_baseline(d, hygiene_keys)
    return DomainConfig(
        name=d["name"],
        fqn_prefix=d["fqn_prefix"],
        expected_pattern=d.get("expected_pattern"),
        drift_tolerance=float(raw_tol) if raw_tol is not None else None,
        profile=d.get("profile"),
        params=_load_params(d),
        hygiene_baseline=hygiene_baseline,
        enforce=bool(d.get("enforce", enforce_default)),
    )


def _ideal_layer(pattern_name: str, layer: dict[str, Any]) -> tuple[float, ...]:
    """Convert one {triad: share} mapping into a TRIAD_ORDER-aligned tuple."""
    _validate_mapping(layer, f"Pattern '{pattern_name}' ideal layer")
    unknown = set(layer) - set(TRIAD_ORDER)
    if unknown:
        msg = f"Pattern '{pattern_name}' ideal names unknown triad(s) {sorted(unknown)}."
        raise ValueError(msg)
    values = tuple(float(layer.get(name, 0.0)) for name in TRIAD_ORDER)
    if abs(sum(values) - 1.0) > 1e-9:
        msg = f"Pattern '{pattern_name}' ideal layer must sum to 1.0, got {sum(values)}."
        raise ValueError(msg)
    return values


def _merge_params(template: dict[str, Any], domain: DomainConfig) -> dict[str, float]:
    """Merge template parameter defaults with domain overrides; unknown keys fail loud.

    Raises ValueError if domain.params contains keys not declared in template.params.
    """
    declared = {
        k: float(v)
        for k, v in _params_mapping(template, f"Pattern '{domain.expected_pattern}'").items()
    }
    unknown = set(domain.params) - set(declared)
    if unknown:
        msg = (
            f"Domain '{domain.name}' overrides undeclared parameter(s) "
            f"{sorted(unknown)} for pattern '{domain.expected_pattern}'."
        )
        raise ValueError(msg)
    return {**declared, **domain.params}


def _resolve_value(value: str | float | int, params: dict[str, float]) -> float:
    """Return a numeric constraint value, substituting a $name placeholder if present.

    Raises ValueError if a $placeholder references an undeclared parameter.
    """
    if isinstance(value, str) and value.startswith("$"):
        key = value[1:]
        if key not in params:
            msg = f"Constraint placeholder '${key}' has no declared parameter."
            raise ValueError(msg)
        return params[key]
    return float(value)


def parse_constraints(
    template: dict[str, Any], params: dict[str, float]
) -> dict[str, tuple[str, float]]:
    """Extract (operator, value) pairs for each constrained component, resolving $params."""
    result: dict[str, tuple[str, float]] = {}
    for name in COMPONENT_NAMES:
        constraint = template.get(name)
        if constraint is None or not isinstance(constraint, dict):
            continue
        for op in ("min", "max", "exact"):
            if op in constraint:
                result[name] = (op, _resolve_value(constraint[op], params))
                break
    return result


class PatternCatalog:
    """patterns.yaml, loaded once and resolved on demand.

    Templates, profiles (drift weights, layer and triad weights), the global
    hygiene block and the domain bindings. Validation failures raise where the
    value is resolved, naming the template, profile or domain at fault.
    """

    def __init__(self, patterns_config: str) -> None:
        """Load and parse the patterns YAML file at patterns_config path."""
        content = yaml.safe_load(Path(patterns_config).read_text(encoding="utf-8"))
        raw: dict[str, Any] = content if isinstance(content, dict) else {}
        self._weights: dict[str, float] = raw.get("drift_weights") or {}
        #: Template name -> its declaration, in file order.
        self.patterns: dict[str, dict[str, Any]] = raw.get("patterns") or {}
        self._project_domains: list[dict[str, Any]] = raw.get("project_domains") or []
        # Measurement profiles (spec §2.3) and global hygiene invariants (§2.1).
        self._profiles: dict[str, dict[str, Any]] = raw.get("profiles") or {}
        self._project_level: list[dict[str, Any]] = raw.get("project_level") or []
        #: The global hygiene constraints, resolved: they take no params.
        self.hygiene: dict[str, tuple[str, float]] = parse_constraints(raw.get("hygiene") or {}, {})

    def load_project_domains(self) -> list[DomainConfig]:
        """Return all project domains declared in patterns.yaml."""
        keys = set(self.hygiene)
        return [_build_domain_config(d, keys, enforce_default=True) for d in self._project_domains]

    def load_project_level(self) -> list[DomainConfig]:
        """Return project-level quotient bindings (spec §3.4); enforce defaults False here."""
        keys = set(self.hygiene)
        return [_build_domain_config(d, keys, enforce_default=False) for d in self._project_level]

    def weights_for(self, domain: DomainConfig) -> dict[str, float]:
        """Return the drift weights for a domain: its profile's, or the top-level default.

        Raises ValueError if the named profile does not exist in the config.
        """
        if domain.profile is None:
            return self._weights
        profile = self._profiles.get(domain.profile)
        if profile is None:
            msg = f"Domain '{domain.name}' names unknown profile '{domain.profile}'."
            raise ValueError(msg)
        weights: dict[str, float] = profile.get("drift_weights") or {}
        return weights

    def ideal_for(self, pattern_name: str) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
        """Return (ideal_imports, ideal_calls) 13-tuples for a template, or None.

        None means the template declares no ideal block — the domain scores on
        the v1 path. Raises ValueError on unknown triad keys, layers other
        than imports/calls, or a layer that does not sum to 1.0.
        """
        template = self.patterns.get(pattern_name) or {}
        ideal = template.get("ideal")
        if ideal is None:
            return None
        if not isinstance(ideal, dict) or set(ideal) != {"imports", "calls"}:
            msg = f"Pattern '{pattern_name}' ideal must declare exactly imports and calls."
            raise ValueError(msg)
        return _ideal_layer(pattern_name, ideal["imports"]), _ideal_layer(
            pattern_name, ideal["calls"]
        )

    def layers_for(self, profile_name: str) -> dict[str, float] | None:
        """Return validated layer weights for a profile, or None when undeclared."""
        profile = self._profiles.get(profile_name) or {}
        layers = profile.get("layers")
        if layers is None:
            return None
        _validate_mapping(layers, f"Profile '{profile_name}' layers")
        if set(layers) != {"imports", "calls", "gates"}:
            msg = f"Profile '{profile_name}' layers must declare imports, calls, gates."
            raise ValueError(msg)
        result = {k: float(v) for k, v in layers.items()}
        if any(v < 0.0 for v in result.values()):
            msg = f"Profile '{profile_name}' layers must be non-negative, got {result}."
            raise ValueError(msg)
        if abs(sum(result.values()) - 1.0) > 1e-9:
            msg = f"Profile '{profile_name}' layers must sum to 1.0, got {sum(result.values())}."
            raise ValueError(msg)
        return result

    def triad_weights_for(self, profile_name: str) -> tuple[float, ...]:
        """Per-triad w_i for a profile; unlisted triads default to 1.0 (spec §3.3)."""
        profile = self._profiles.get(profile_name) or {}
        declared: dict[str, Any] = _validate_mapping(
            profile.get("triad_weights") or {}, f"Profile '{profile_name}' triad_weights"
        )
        unknown = set(declared) - set(TRIAD_ORDER)
        if unknown:
            msg = (
                f"Profile '{profile_name}' triad_weights names unknown triad(s) {sorted(unknown)}."
            )
            raise ValueError(msg)
        weights = tuple(float(declared.get(name, 1.0)) for name in TRIAD_ORDER)
        if any(w < 0.0 for w in weights):
            msg = f"Profile '{profile_name}' triad_weights must be non-negative."
            raise ValueError(msg)
        return weights

    def template_constraints(self, domain: DomainConfig) -> dict[str, tuple[str, float]]:
        """The domain's template constraints with its params substituted; {} when hygiene-only.

        Raises ValueError if the named pattern is missing or a domain param is
        undeclared, and TypeError if the pattern is not a mapping of constraints.
        """
        if domain.expected_pattern is None:
            return {}
        found = self.patterns.get(domain.expected_pattern)
        if found is None:
            msg = f"Expected pattern '{domain.expected_pattern}' not found in patterns config."
            raise ValueError(msg)
        if not isinstance(found, dict):
            msg = f"Pattern '{domain.expected_pattern}' must be a mapping of constraints."
            raise TypeError(msg)
        return parse_constraints(found, _merge_params(found, domain))
