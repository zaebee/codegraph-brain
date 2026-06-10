"""DomainConfig, DriftReport, and DriftScorer for architectural drift measurement."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from cgis.query.fingerprint import PatternFingerprint

_COMPONENT_NAMES = (
    "hub_count",
    "star_count",
    "chain_len",
    "dag_depth",
    "router_count",
    "cycle_ratio",
    "unresolved_ratio",
)

_STATUS_WARNING = 0.20
_STATUS_CRITICAL = 0.50


@dataclass(frozen=True)
class DomainConfig:
    """Project-level domain expectation loaded from patterns.yaml."""

    name: str
    fqn_prefix: str
    expected_pattern: str | None
    drift_tolerance: float
    profile: str | None = None
    params: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DriftReport:
    """Per-domain drift analysis result."""

    domain: str
    fqn_prefix: str
    expected_pattern: str
    actual: PatternFingerprint
    ideal: PatternFingerprint
    drift_score: float
    violations: list[str]
    status: Literal["clean", "warning", "critical"]
    tolerance: float


def _classify(score: float) -> Literal["clean", "warning", "critical"]:
    """Return status label based on drift score thresholds."""
    if score < _STATUS_WARNING:
        return "clean"
    if score < _STATUS_CRITICAL:
        return "warning"
    return "critical"


class DriftScorer:
    """Load patterns.yaml and score actual PatternFingerprints against ideal templates."""

    def __init__(self, patterns_config: str) -> None:
        """Load and parse the patterns YAML file at patterns_config path."""
        content = yaml.safe_load(Path(patterns_config).read_text())
        raw: dict[str, Any] = content if isinstance(content, dict) else {}
        self._weights: dict[str, float] = raw.get("drift_weights") or {}
        self._patterns: dict[str, dict[str, Any]] = raw.get("patterns") or {}
        self._project_domains: list[dict[str, Any]] = raw.get("project_domains") or []
        # Read by profile selection and hygiene merging (Tasks 2 and 4 of Part A).
        self._profiles: dict[str, dict[str, Any]] = raw.get("profiles") or {}
        self._hygiene: dict[str, Any] = raw.get("hygiene") or {}

    @staticmethod
    def _load_params(d: dict[str, Any]) -> dict[str, float]:
        """Parse a domain binding's params block; non-numeric values fail loud.

        Raises TypeError if any param value is not numeric (int, float, or bool).
        """
        params: dict[str, float] = {}
        for k, v in (d.get("params") or {}).items():
            if not isinstance(v, (int, float)):
                msg = f"Domain '{d.get('name', '?')}' param '{k}' must be numeric, got {v!r}."
                raise TypeError(msg)
            params[k] = float(v)
        return params

    def load_project_domains(self) -> list[DomainConfig]:
        """Return all project domains declared in patterns.yaml."""
        return [
            DomainConfig(
                name=d["name"],
                fqn_prefix=d["fqn_prefix"],
                expected_pattern=d.get("expected_pattern"),
                drift_tolerance=float(d["drift_tolerance"]),
                profile=d.get("profile"),
                params=self._load_params(d),
            )
            for d in self._project_domains
        ]

    def _weights_for(self, domain: DomainConfig) -> dict[str, float]:
        """Return the drift weights for a domain: its profile's, or the top-level default.

        If domain.profile is set, look it up in self._profiles and return its
        drift_weights. If profile is not found, raise ValueError. If domain.profile
        is None, return the top-level self._weights.

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

    @staticmethod
    def _merge_params(template: dict[str, Any], domain: DomainConfig) -> dict[str, float]:
        """Merge template parameter defaults with domain overrides; unknown keys fail loud.

        Raises ValueError if domain.params contains keys not declared in template.params.
        """
        declared = {k: float(v) for k, v in (template.get("params") or {}).items()}
        unknown = set(domain.params) - set(declared)
        if unknown:
            msg = (
                f"Domain '{domain.name}' overrides undeclared parameter(s) "
                f"{sorted(unknown)} for pattern '{domain.expected_pattern}'."
            )
            raise ValueError(msg)
        return {**declared, **domain.params}

    @staticmethod
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

    def score(self, actual: PatternFingerprint, domain: DomainConfig) -> DriftReport:
        """Compute the drift score and return a DriftReport."""
        expected_pattern = domain.expected_pattern
        if expected_pattern is None:
            msg = (
                f"Domain '{domain.name}' has no expected_pattern "
                "(hygiene-only domains land in a later commit)."
            )
            raise NotImplementedError(msg)
        template = self._patterns.get(expected_pattern)
        if template is None:
            msg = f"Expected pattern '{expected_pattern}' not found in patterns config."
            raise ValueError(msg)
        if not isinstance(template, dict):
            msg = f"Pattern '{expected_pattern}' must be a mapping of constraints."
            raise TypeError(msg)
        params = self._merge_params(template, domain)
        constraints = self._parse_constraints(template, params)

        if not constraints:
            return self._zero_drift_report(actual, expected_pattern, domain)

        weights = self._weights_for(domain)
        total_weight = sum(weights.get(name, 0.0) for name in constraints)
        violations: list[str] = []
        drift_sum = 0.0
        ideal_overrides: dict[str, float] = {}

        for name, (op, value) in constraints.items():
            actual_val = float(getattr(actual, name))
            ideal_val, norm, raw, violation = self._score_constraint(name, op, value, actual_val)
            if violation:
                violations.append(violation)
            ideal_overrides[name] = ideal_val
            component_drift = min(raw / norm, 1.0)
            weight = (
                weights.get(name, 0.0) / total_weight
                if total_weight > 0.0
                else 1.0 / len(constraints)
            )
            drift_sum += weight * component_drift

        ideal_fp = PatternFingerprint(
            domain=domain.fqn_prefix,
            hub_count=int(ideal_overrides.get("hub_count", 0)),
            star_count=int(ideal_overrides.get("star_count", 0)),
            chain_len=float(ideal_overrides.get("chain_len", 0.0)),
            dag_depth=int(ideal_overrides.get("dag_depth", 0)),
            router_count=int(ideal_overrides.get("router_count", 0)),
            cycle_ratio=float(ideal_overrides.get("cycle_ratio", 0.0)),
            unresolved_ratio=float(ideal_overrides.get("unresolved_ratio", 0.0)),
        )

        return DriftReport(
            domain=domain.name,
            fqn_prefix=domain.fqn_prefix,
            expected_pattern=expected_pattern,
            actual=actual,
            ideal=ideal_fp,
            drift_score=round(drift_sum, 6),
            violations=violations,
            status=_classify(drift_sum),
            tolerance=domain.drift_tolerance,
        )

    def _zero_drift_report(
        self, actual: PatternFingerprint, expected_pattern: str, domain: DomainConfig
    ) -> DriftReport:
        """Return a clean zero-drift report for domains with no constraints."""
        ideal_fp = PatternFingerprint(
            domain=domain.fqn_prefix,
            hub_count=0,
            star_count=0,
            chain_len=0.0,
            dag_depth=0,
            router_count=0,
            cycle_ratio=0.0,
            unresolved_ratio=0.0,
        )
        return DriftReport(
            domain=domain.name,
            fqn_prefix=domain.fqn_prefix,
            expected_pattern=expected_pattern,
            actual=actual,
            ideal=ideal_fp,
            drift_score=0.0,
            violations=[],
            status="clean",
            tolerance=domain.drift_tolerance,
        )

    @staticmethod
    def _score_constraint(
        name: str, op: str, value: float, actual_val: float
    ) -> tuple[float, float, float, str | None]:
        """Compute ideal_val, norm, raw drift, and optional violation message for one constraint."""
        violation: str | None = None
        if op == "min":
            ideal_val = value
            norm = max(ideal_val, 1.0)
            raw = max(0.0, ideal_val - actual_val)
            if actual_val < value:
                violation = f"{name} {actual_val} < min {value}"
        elif op == "max":
            ideal_val = 0.0
            norm = max(value, 1.0)
            raw = max(0.0, actual_val - value)
            if actual_val > value:
                violation = f"{name} {actual_val} > max {value}"
        else:  # exact
            ideal_val = value
            norm = max(ideal_val, 1.0)
            raw = abs(actual_val - ideal_val)
            if actual_val != value:
                violation = f"{name} {actual_val} != exact {value}"
        return ideal_val, norm, raw, violation

    def _parse_constraints(
        self, template: dict[str, Any], params: dict[str, float]
    ) -> dict[str, tuple[str, float]]:
        """Extract (operator, value) pairs for each constrained component, resolving $params."""
        result: dict[str, tuple[str, float]] = {}
        for name in _COMPONENT_NAMES:
            constraint = template.get(name)
            if constraint is None or not isinstance(constraint, dict):
                continue
            for op in ("min", "max", "exact"):
                if op in constraint:
                    result[name] = (op, self._resolve_value(constraint[op], params))
                    break
        return result
