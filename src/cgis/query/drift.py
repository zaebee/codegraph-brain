"""DomainConfig, DriftReport, and DriftScorer for architectural drift measurement."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from cgis.query.fingerprint import PatternFingerprint
from cgis.query.triads import TRIAD_ORDER, ZERO_TRIADS, tv_distance

_COMPONENT_NAMES = (
    "hub_count",
    "star_count",
    "chain_len",
    "dag_depth",
    "router_count",
    "cycle_ratio",
    "unresolved_ratio",
)

_CALLS_LAYER = frozenset({"hub_count", "star_count", "chain_len", "router_count"})

_TRIAD_VIOLATION_THRESHOLD = 0.05


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


@dataclass(frozen=True)
class FitQuality:
    """How well the closest alphabet template matches a domain's shape (#177).

    ``residual`` is the domain's tolerance-free distance to a template's ideal
    (``DriftScorer.fit_templates``) — it answers "clean because it MATCHES an
    archetype" vs "clean because tolerance is loose". ``band``:
    ``good`` (≤ good-threshold), ``weak``, or ``none`` (> max_residual — no
    template in the closed alphabet captures this domain: a genuine grab-bag,
    a mesh/tangle, or a gap the alphabet should fill).
    """

    nearest_template: str
    nearest_residual: float
    runner_up_template: str | None
    runner_up_residual: float | None
    band: Literal["good", "weak", "none"]


@dataclass(frozen=True)
class DriftReport:
    """Per-domain drift analysis result."""

    domain: str
    fqn_prefix: str
    expected_pattern: str | None
    actual: PatternFingerprint
    ideal: PatternFingerprint
    drift_score: float
    violations: list[str]
    status: Literal["clean", "warning", "critical", "gate_failed", "empty", "no_signal"]
    tolerance: float
    tv_imports: float | None = None
    tv_calls: float | None = None
    # Human-readable diagnostic (e.g. closest-prefix suggestions for "empty").
    note: str | None = None
    # Fit quality vs the alphabet (#177); None for hygiene-only/empty/no_signal.
    fit: FitQuality | None = None


def _clip_discount(actual: PatternFingerprint) -> float:
    """Confidence discount clip(1 - unresolved_ratio) to [0, 1].

    Clipped defensively: a hand-built fingerprint could carry a ratio outside
    [0, 1]; negative effective weights must never occur.
    """
    return max(0.0, min(1.0 - actual.unresolved_ratio, 1.0))


def _classify(score: float, tolerance: float) -> Literal["clean", "warning", "critical"]:
    """Status from the score RELATIVE to the binding's effective tolerance (#170B)."""
    if score > tolerance:
        return "critical"
    if score > 0.75 * tolerance:
        return "warning"
    return "clean"


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


class DriftScorer:
    """Load patterns.yaml and score actual PatternFingerprints against ideal templates.

    Global hygiene invariants apply to every domain (template constraints win per
    component); CALLS-derived component weights are discounted by
    (1 - unresolved_ratio) before renormalization.
    """

    def __init__(self, patterns_config: str) -> None:
        """Load and parse the patterns YAML file at patterns_config path."""
        content = yaml.safe_load(Path(patterns_config).read_text())
        raw: dict[str, Any] = content if isinstance(content, dict) else {}
        self._weights: dict[str, float] = raw.get("drift_weights") or {}
        self._patterns: dict[str, dict[str, Any]] = raw.get("patterns") or {}
        self._project_domains: list[dict[str, Any]] = raw.get("project_domains") or []
        # Measurement profiles (spec §2.3) and global hygiene invariants (§2.1).
        self._profiles: dict[str, dict[str, Any]] = raw.get("profiles") or {}
        self._hygiene: dict[str, Any] = raw.get("hygiene") or {}
        self._project_level: list[dict[str, Any]] = raw.get("project_level") or []

    @staticmethod
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

    def _build_domain_config(self, d: dict[str, Any], *, enforce_default: bool) -> DomainConfig:
        """Build one DomainConfig from a YAML binding dict."""
        raw_tol = d.get("drift_tolerance")
        tolerance = float(raw_tol) if raw_tol is not None else None
        hygiene_baseline = self._parse_hygiene_baseline(d)
        return DomainConfig(
            name=d["name"],
            fqn_prefix=d["fqn_prefix"],
            expected_pattern=d.get("expected_pattern"),
            drift_tolerance=tolerance,
            profile=d.get("profile"),
            params=self._load_params(d),
            hygiene_baseline=hygiene_baseline,
            enforce=bool(d.get("enforce", enforce_default)),
        )

    def _parse_hygiene_baseline(self, d: dict[str, Any]) -> dict[str, float]:
        """Parse and validate the hygiene_baseline block from a domain binding dict.

        Raises ValueError if any baseline key is not a valid hygiene constraint key.
        """
        raw = d.get("hygiene_baseline")
        if raw is None:
            return {}
        mapping = _validate_mapping(raw, f"Domain '{d.get('name', '?')}' hygiene_baseline")
        hygiene_keys = set(self._parse_constraints(self._hygiene, {}).keys())
        result: dict[str, float] = {}
        for key, val in mapping.items():
            if key not in hygiene_keys:
                valid = sorted(hygiene_keys)
                msg = (
                    f"hygiene_baseline key '{key}' in domain '{d.get('name', '?')}' "
                    f"names no hygiene constraint; valid keys: {valid}"
                )
                raise ValueError(msg)
            result[key] = float(val)
        return result

    def load_project_domains(self) -> list[DomainConfig]:
        """Return all project domains declared in patterns.yaml."""
        return [self._build_domain_config(d, enforce_default=True) for d in self._project_domains]

    def load_project_level(self) -> list[DomainConfig]:
        """Return project-level quotient bindings (spec §3.4); enforce defaults False here."""
        return [self._build_domain_config(d, enforce_default=False) for d in self._project_level]

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

    def ideal_for(self, pattern_name: str) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
        """Return (ideal_imports, ideal_calls) 13-tuples for a template, or None.

        None means the template declares no ideal block — the domain scores on
        the v1 path. Raises ValueError on unknown triad keys, layers other
        than imports/calls, or a layer that does not sum to 1.0.
        """
        template = self._patterns.get(pattern_name) or {}
        ideal = template.get("ideal")
        if ideal is None:
            return None
        if not isinstance(ideal, dict) or set(ideal) != {"imports", "calls"}:
            msg = f"Pattern '{pattern_name}' ideal must declare exactly imports and calls."
            raise ValueError(msg)
        return self._ideal_layer(pattern_name, ideal["imports"]), self._ideal_layer(
            pattern_name, ideal["calls"]
        )

    @staticmethod
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

    @staticmethod
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

    def _resolve_template(self, domain: DomainConfig) -> tuple[dict[str, Any], dict[str, float]]:
        """Return the domain's bound template and merged params; ({}, {}) when hygiene-only.

        Raises ValueError if the named pattern is missing and TypeError if it
        is not a mapping of constraints.
        """
        if domain.expected_pattern is None:
            return {}, {}
        found = self._patterns.get(domain.expected_pattern)
        if found is None:
            msg = f"Expected pattern '{domain.expected_pattern}' not found in patterns config."
            raise ValueError(msg)
        if not isinstance(found, dict):
            msg = f"Pattern '{domain.expected_pattern}' must be a mapping of constraints."
            raise TypeError(msg)
        return found, self._merge_params(found, domain)

    def score(
        self,
        actual: PatternFingerprint,
        domain: DomainConfig,
        default_tolerance: float = 0.50,
    ) -> DriftReport:
        """Compute the drift score and return a DriftReport (v2 when configured).

        Hygiene and template constraints are evaluated SEPARATELY (spec §2.2):
        hygiene violations against operator-aware baseline-relaxed bounds force
        status='gate_failed'; template violations keep score-driven classification.
        The SCORE math (merged constraint dict) is unchanged from prior behaviour.

        ``default_tolerance`` is the fallback when ``domain.drift_tolerance is None``
        — callers (CLI, MCP) pass ``max_drift`` here; the per-domain value takes
        precedence when declared (#170B).
        """
        tolerance_eff = (
            domain.drift_tolerance if domain.drift_tolerance is not None else default_tolerance
        )
        if actual.node_count == 0:
            return self._signal_report(actual, domain, status="empty", tolerance_eff=tolerance_eff)
        if actual.edge_count == 0:
            return self._signal_report(
                actual, domain, status="no_signal", tolerance_eff=tolerance_eff
            )
        template, params = self._resolve_template(domain)
        hygiene = self._parse_constraints(self._hygiene, {})
        template_constraints = self._parse_constraints(template, params)
        # SCORE math unchanged: merged dict feeds v1/v2 paths.
        constraints = {**hygiene, **template_constraints}

        # STATUS: hygiene evaluated separately with baseline-relaxed effective bounds.
        hygiene_eff = self._apply_baseline(hygiene, domain.hygiene_baseline)
        gate_violations, acknowledged = self._hygiene_check(actual, hygiene_eff, domain)

        ideal = None if domain.expected_pattern is None else self.ideal_for(domain.expected_pattern)
        layers = None if domain.profile is None else self.layers_for(domain.profile)
        hygiene_keys: frozenset[str] = frozenset(hygiene.keys())
        if ideal is None or layers is None:
            return self._score_v1(
                actual,
                domain,
                constraints,
                self._weights_for(domain),
                hygiene_keys=hygiene_keys,
                gate_violations=gate_violations,
                acknowledged=acknowledged,
                tolerance_eff=tolerance_eff,
            )
        return self._score_v2(
            actual,
            domain,
            constraints,
            ideal,
            layers,
            hygiene_keys=hygiene_keys,
            gate_violations=gate_violations,
            acknowledged=acknowledged,
            tolerance_eff=tolerance_eff,
        )

    def fit_templates(self, actual: PatternFingerprint, profile: str) -> list[tuple[str, float]]:
        """Return ``[(template_name, residual)]`` sorted by (residual asc, name asc).

        The residual is ``actual``'s drift score against each template's ideal
        under a synthetic binding with ``drift_tolerance=1.0`` — a pure,
        status-free distance ("how far is this shape from the archetype",
        independent of any tolerance). Iterates THIS scorer's loaded templates,
        so it reflects whatever alphabet ``patterns.yaml`` declares. This is the
        canonical "distance to template" reused by both fit-quality reporting
        (#177) and init-ontology labelling (#174) — one source of truth.

        Returns the FULL ``drift_score`` (gates included) — this is the value
        init-ontology turns into a domain's tolerance, so the proposed ontology
        round-trips even on cyclic domains (#174). Fit-quality reporting bands
        on ``shape_residual`` instead (gate-free), so a cycle inflates the
        score here without ever printing "no template fits".
        """
        fits = [
            (
                t_name,
                self.score(actual, self._fit_config(actual.domain, t_name, profile)).drift_score,
            )
            for t_name in self._patterns
        ]
        fits.sort(key=lambda pair: (pair[1], pair[0]))
        return fits

    @staticmethod
    def _fit_config(domain: str, template: str, profile: str) -> DomainConfig:
        """Synthetic domain binding used to measure distance to one template."""
        return DomainConfig(
            name=domain,
            fqn_prefix=domain,
            expected_pattern=template,
            profile=profile,
            drift_tolerance=1.0,
        )

    def shape_residual(
        self, actual: PatternFingerprint, profile: str, template: str
    ) -> float | None:
        """Gate-FREE layered-TV distance to one template's ideal, or None (#177 fit band).

        Unlike ``fit_templates`` (which returns the full ``drift_score`` so
        init-ontology's tolerance covers the gate term too, #174 round-trip),
        this excludes the hygiene/gate contribution: a domain that genuinely
        fits an archetype but carries a cycle is NOT banded "no template fits"
        — the cycle is the gate's story, not the shape's. Combines the two TV
        layers exactly as ``_score_v2`` does, minus the gates layer.

        Returns ``None`` when there is no trusted shape weight at all — a v1
        profile (no ``layers``), an empty census on both layers, or fully
        unresolved calls (``discount == 0``) with no imports layer. Banding
        that 0.0 would read as "clean because it MATCHES an archetype" when the
        truth is "no signal to fit" (review #1 / gemini); the caller maps None
        to ``fit=None``.
        """
        report = self.score(actual, self._fit_config(actual.domain, template, profile))
        layers = self.layers_for(profile) or {}
        discount = _clip_discount(actual)
        imp_w = layers.get("imports", 0.0) if report.tv_imports is not None else 0.0
        cal_w = layers.get("calls", 0.0) * discount if report.tv_calls is not None else 0.0
        total = imp_w + cal_w
        if total <= 0.0:
            return None
        return (imp_w * (report.tv_imports or 0.0) + cal_w * (report.tv_calls or 0.0)) / total

    def _score_v1(
        self,
        actual: PatternFingerprint,
        domain: DomainConfig,
        constraints: dict[str, tuple[str, float]],
        weights: dict[str, float],
        *,
        hygiene_keys: frozenset[str],
        gate_violations: list[str],
        acknowledged: list[str],
        tolerance_eff: float,
    ) -> DriftReport:
        """V1 scoring: per-constraint weighted drift with CALLS-layer discount."""
        if not constraints:
            return self._zero_drift_report(
                actual,
                domain,
                gate_violations=gate_violations,
                acknowledged=acknowledged,
                tolerance_eff=tolerance_eff,
            )

        drift_sum, raw_violations, ideal_overrides = self._weighted_constraint_drift(
            actual, constraints, weights
        )
        # Filter merged-path violations for hygiene keys that _hygiene_check already
        # reported — suppress double-reporting (e.g. "cycle_ratio 0.07 > max 0.0"
        # alongside "hygiene cycle_ratio 0.07 violates max 0.0").
        # Only keys that produced a gate_violations or acknowledged entry are suppressed;
        # a key that appears in both hygiene AND template but only breaches the TEMPLATE
        # bound (not the hygiene bound) is NOT suppressed — its template violation is the
        # sole reporter in that case.
        hygiene_reported_keys = {
            k for k in hygiene_keys if any(k in msg for msg in gate_violations + acknowledged)
        }
        violations = [
            v
            for v in raw_violations
            if not any(v.startswith(k + " ") for k in hygiene_reported_keys)
        ]

        all_violations = violations + gate_violations + acknowledged
        status: Literal["clean", "warning", "critical", "gate_failed", "empty", "no_signal"] = (
            "gate_failed" if gate_violations else _classify(drift_sum, tolerance_eff)
        )

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
            drift_score=drift_sum,
            violations=all_violations,
            status=status,
            tolerance=tolerance_eff,
        )

    def _weighted_constraint_drift(
        self,
        actual: PatternFingerprint,
        constraints: dict[str, tuple[str, float]],
        weights: dict[str, float],
        discount: float | None = None,
    ) -> tuple[float, list[str], dict[str, float]]:
        """Shared per-constraint loop: CALLS-layer discount, renorm, violation strings.

        Returns (drift_sum, violations, ideal_overrides).
        discount defaults to clip(1 - actual.unresolved_ratio) when not supplied.
        """
        if discount is None:
            discount = _clip_discount(actual)

        raw_weights = {name: weights.get(name, 0.0) for name in constraints}
        eff_weights = {
            name: w * discount if name in _CALLS_LAYER else w for name, w in raw_weights.items()
        }
        raw_total = sum(raw_weights.values())
        eff_total = sum(eff_weights.values())
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
            if raw_total > 0.0:
                weight = eff_weights[name] / eff_total if eff_total > 0.0 else 0.0
            else:
                weight = 1.0 / len(constraints)
            drift_sum += weight * component_drift

        return drift_sum, violations, ideal_overrides

    def _gate_drift(
        self,
        actual: PatternFingerprint,
        domain: DomainConfig,
        gates: dict[str, tuple[str, float]],
        discount: float,
    ) -> tuple[float, list[str]]:
        """Compute drift for gate constraints only (v2 path), reusing the shared mechanism."""
        if not gates:
            return 0.0, []
        weights = self._weights_for(domain)
        gate_drift, violations, _ = self._weighted_constraint_drift(
            actual, gates, weights, discount=discount
        )
        return gate_drift, violations

    def _score_v2(
        self,
        actual: PatternFingerprint,
        domain: DomainConfig,
        gates: dict[str, tuple[str, float]],
        ideal: tuple[tuple[float, ...], tuple[float, ...]],
        layers: dict[str, float],
        *,
        hygiene_keys: frozenset[str],
        gate_violations: list[str],
        acknowledged: list[str],
        tolerance_eff: float,
    ) -> DriftReport:
        """Fingerprint v2 drift: layered TV distance + hard gates (spec §3.3).

        Every constraint reaching this path is treated as a hard gate — the
        topological shape itself is measured by the TV terms, not constraints.
        Hygiene gate violations are threaded in from score() and take precedence
        over score-driven classification (spec §2.2).
        """
        if domain.profile is None:  # layers presence implies a profile
            msg = "v2 scoring requires a profile on the domain binding."
            raise RuntimeError(msg)
        triad_w = self.triad_weights_for(domain.profile)
        discount = _clip_discount(actual)
        violations: list[str] = []

        tv_imp: float | None = None
        if actual.t_imports != ZERO_TRIADS:
            tv_imp, contribs = tv_distance(actual.t_imports, ideal[0], triad_w)
            violations.extend(
                self._triad_violations("T_imports", actual.t_imports, ideal[0], contribs)
            )
        tv_cal: float | None = None
        if actual.t_calls != ZERO_TRIADS:
            tv_cal, contribs = tv_distance(actual.t_calls, ideal[1], triad_w)
            violations.extend(self._triad_violations("T_calls", actual.t_calls, ideal[1], contribs))

        gate_drift, raw_v1_violations = self._gate_drift(actual, domain, gates, discount)
        # Filter v1 gate violations for hygiene keys that _hygiene_check already reported.
        # Same logic as _score_v1: only suppress keys that gate_violations/acknowledged
        # already covers; template-only violations on hygiene-named keys are kept.
        hygiene_reported_keys = {
            k for k in hygiene_keys if any(k in msg for msg in gate_violations + acknowledged)
        }
        violations.extend(
            v
            for v in raw_v1_violations
            if not any(v.startswith(k + " ") for k in hygiene_reported_keys)
        )
        all_violations = violations + gate_violations + acknowledged

        eff = {
            "imports": layers["imports"] if tv_imp is not None else 0.0,
            "calls": layers["calls"] * discount if tv_cal is not None else 0.0,
            "gates": layers["gates"] if gates else 0.0,
        }
        terms = {
            "imports": 0.0 if tv_imp is None else tv_imp,
            "calls": 0.0 if tv_cal is None else tv_cal,
            "gates": gate_drift,
        }
        total = sum(eff.values())
        drift = sum(eff[k] * terms[k] for k in eff) / total if total > 0.0 else 0.0

        status: Literal["clean", "warning", "critical", "gate_failed", "empty", "no_signal"] = (
            "gate_failed" if gate_violations else _classify(drift, tolerance_eff)
        )

        return DriftReport(
            domain=domain.name,
            fqn_prefix=domain.fqn_prefix,
            expected_pattern=domain.expected_pattern,
            actual=actual,
            ideal=self._ideal_fingerprint_v2(domain, ideal),
            drift_score=drift,
            violations=all_violations,
            status=status,
            tolerance=tolerance_eff,
            tv_imports=tv_imp,
            tv_calls=tv_cal,
        )

    @staticmethod
    def _triad_violations(
        layer: str,
        actual: tuple[float, ...],
        ideal: tuple[float, ...],
        contribs: list[tuple[str, float]],
    ) -> list[str]:
        """Render triad terms contributing ≥ threshold as violation strings."""
        return [
            f"{layer}[{name}]={actual[i]:.2f} vs ideal {ideal[i]:.2f} (+{c:.2f})"
            for i, (name, c) in enumerate(contribs)
            if c >= _TRIAD_VIOLATION_THRESHOLD
        ]

    @staticmethod
    def _ideal_fingerprint_v2(
        domain: DomainConfig,
        ideal: tuple[tuple[float, ...], tuple[float, ...]],
    ) -> PatternFingerprint:
        """Ideal fingerprint carrying the template's triad points (v1 fields zero)."""
        return PatternFingerprint(
            domain=domain.fqn_prefix,
            hub_count=0,
            star_count=0,
            chain_len=0.0,
            dag_depth=0,
            router_count=0,
            cycle_ratio=0.0,
            unresolved_ratio=0.0,
            t_imports=ideal[0],
            t_calls=ideal[1],
        )

    def _signal_report(
        self,
        actual: PatternFingerprint,
        domain: DomainConfig,
        status: Literal["empty", "no_signal"],
        *,
        tolerance_eff: float,
    ) -> DriftReport:
        """Report for a domain with nothing to score: matched 0 nodes (empty)
        or matched nodes but 0 intra-domain edges (no_signal). Score is 0.0 by
        definition — the gate handles 'empty' separately (spec §2.3)."""
        return DriftReport(
            domain=domain.name,
            fqn_prefix=domain.fqn_prefix,
            expected_pattern=domain.expected_pattern,
            actual=actual,
            ideal=actual,
            drift_score=0.0,
            violations=[],
            status=status,
            tolerance=tolerance_eff,
        )

    def _zero_drift_report(
        self,
        actual: PatternFingerprint,
        domain: DomainConfig,
        *,
        gate_violations: list[str],
        acknowledged: list[str],
        tolerance_eff: float,
    ) -> DriftReport:
        """Return a zero-drift report for domains with no constraints.

        Gate violations still force 'gate_failed' even when there are no
        template constraints to score against.
        """
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
        all_violations = gate_violations + acknowledged
        status: Literal["clean", "warning", "critical", "gate_failed", "empty", "no_signal"] = (
            "gate_failed" if gate_violations else "clean"
        )
        return DriftReport(
            domain=domain.name,
            fqn_prefix=domain.fqn_prefix,
            expected_pattern=domain.expected_pattern,
            actual=actual,
            ideal=ideal_fp,
            drift_score=0.0,
            violations=all_violations,
            status=status,
            tolerance=tolerance_eff,
        )

    @staticmethod
    def _apply_baseline(
        hygiene: dict[str, tuple[str, float]],
        baseline: dict[str, float],
    ) -> dict[str, tuple[str, float]]:
        """Relax hygiene bounds by the domain's acknowledged debt (spec §2.2).

        Operator-aware: max-bounds take max(global, baseline), min-bounds take
        min(global, baseline), exact-bounds are overridden — a baseline can
        only ever RELAX, never tighten.
        """
        out = dict(hygiene)
        for key, ack in baseline.items():
            op, bound = out[key]  # key validity enforced at load time
            if op == "max":
                out[key] = (op, max(bound, ack))
            elif op == "min":
                out[key] = (op, min(bound, ack))
            else:  # exact
                out[key] = (op, ack)
        return out

    @staticmethod
    def _hygiene_check(
        actual: PatternFingerprint,
        hygiene_eff: dict[str, tuple[str, float]],
        domain: DomainConfig,
    ) -> tuple[list[str], list[str]]:
        """Return (breaches, acknowledgments) against the EFFECTIVE hygiene bounds.

        A breach of any effective bound forces status='gate_failed' upstream.
        A measurement over the GLOBAL bound but within the acknowledged
        baseline produces a visibility note, not a breach.

        Comparison directions mirror ``_score_constraint`` / ``_weighted_constraint_drift``:
        max → actual > bound; min → actual < bound; exact → actual != bound.
        """
        breaches: list[str] = []
        acknowledged: list[str] = []
        for key, (op, bound) in hygiene_eff.items():
            value = float(getattr(actual, key))
            violated = (
                (op == "max" and value > bound)
                or (op == "min" and value < bound)
                or (op == "exact" and value != bound)
            )
            if violated:
                breaches.append(f"hygiene {key} {value:.4f} violates {op} {bound}")
            elif key in domain.hygiene_baseline:
                acknowledged.append(
                    f"{key} {value:.4f} acknowledged (baseline {domain.hygiene_baseline[key]})"
                )
        return breaches, acknowledged

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
