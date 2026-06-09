"""DomainConfig, DriftReport, and DriftScorer for architectural drift measurement."""

from __future__ import annotations

from dataclasses import dataclass
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
    expected_pattern: str
    drift_tolerance: float


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
        raw: dict[str, Any] = yaml.safe_load(Path(patterns_config).read_text()) or {}
        self._weights: dict[str, float] = raw.get("drift_weights") or {}
        self._patterns: dict[str, dict[str, Any]] = raw.get("patterns") or {}
        self._project_domains: list[dict[str, Any]] = raw.get("project_domains") or []

    def load_project_domains(self) -> list[DomainConfig]:
        """Return all project domains declared in patterns.yaml."""
        return [
            DomainConfig(
                name=d["name"],
                fqn_prefix=d["fqn_prefix"],
                expected_pattern=d["expected_pattern"],
                drift_tolerance=float(d["drift_tolerance"]),
            )
            for d in self._project_domains
        ]

    def score(self, actual: PatternFingerprint, domain: DomainConfig) -> DriftReport:
        """Compute the drift score and return a DriftReport."""
        template = self._patterns.get(domain.expected_pattern)
        if template is None:
            msg = f"Expected pattern '{domain.expected_pattern}' not found in patterns config."
            raise ValueError(msg)
        constraints = self._parse_constraints(template)

        if not constraints:
            return self._zero_drift_report(actual, domain)

        total_weight = sum(self._weights.get(name, 0.0) for name in constraints)
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
                self._weights.get(name, 0.0) / total_weight
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
            expected_pattern=domain.expected_pattern,
            actual=actual,
            ideal=ideal_fp,
            drift_score=round(drift_sum, 6),
            violations=violations,
            status=_classify(drift_sum),
            tolerance=domain.drift_tolerance,
        )

    def _zero_drift_report(self, actual: PatternFingerprint, domain: DomainConfig) -> DriftReport:
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
            expected_pattern=domain.expected_pattern,
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

    def _parse_constraints(self, template: dict[str, Any]) -> dict[str, tuple[str, float]]:
        """Extract (operator, value) pairs for each constrained component in a template."""
        result: dict[str, tuple[str, float]] = {}
        for name in _COMPONENT_NAMES:
            constraint = template.get(name)
            if constraint is None or not isinstance(constraint, dict):
                continue
            for op in ("min", "max", "exact"):
                if op in constraint:
                    result[name] = (op, float(constraint[op]))
                    break
        return result
