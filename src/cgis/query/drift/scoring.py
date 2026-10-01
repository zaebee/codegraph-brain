"""Drift scoring math: a fingerprint against a resolved template, nothing loaded here (#214).

Pure functions over values `PatternCatalog` has already resolved — constraints,
drift weights, ideal triads, layer and triad weights. `DriftScorer.score` asks
the catalog for those and hands them in; the math below never reads
patterns.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from cgis.query.drift.fingerprint import PatternFingerprint
from cgis.query.drift.triads import ZERO_TRIADS, tv_distance

if TYPE_CHECKING:
    from cgis.query.drift.catalog import DomainConfig

_CALLS_LAYER = frozenset({"hub_count", "star_count", "chain_len", "router_count"})

_TRIAD_VIOLATION_THRESHOLD = 0.05

Status = Literal["clean", "warning", "critical", "gate_failed", "empty", "no_signal"]


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
    status: Status
    tolerance: float
    tv_imports: float | None = None
    tv_calls: float | None = None
    # Human-readable diagnostic (e.g. closest-prefix suggestions for "empty").
    note: str | None = None
    # Fit quality vs the alphabet (#177); None for hygiene-only/empty/no_signal.
    fit: FitQuality | None = None


@dataclass(frozen=True)
class HygieneOutcome:
    """The hygiene gate's verdict, threaded into either scoring path (spec §2.2)."""

    #: Keys of the global hygiene constraints, for de-duplicating violations.
    keys: frozenset[str]
    #: Breaches of the baseline-relaxed bounds; any one forces 'gate_failed'.
    breaches: list[str]
    #: Over the global bound but within the acknowledged baseline: notes only.
    acknowledged: list[str]


def clip_discount(actual: PatternFingerprint) -> float:
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


def _empty_ideal(domain: DomainConfig) -> PatternFingerprint:
    """An ideal fingerprint with every v1 field zero."""
    return PatternFingerprint(
        domain=domain.fqn_prefix,
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )


def signal_report(
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


def apply_baseline(
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


def hygiene_check(
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


def _weighted_constraint_drift(
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
        discount = clip_discount(actual)

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
        ideal_val, norm, raw, violation = _score_constraint(name, op, value, actual_val)
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


def _unreported(violations: list[str], hygiene: HygieneOutcome) -> list[str]:
    """Drop constraint violations for hygiene keys the hygiene gate already reported.

    Suppresses double-reporting ("cycle_ratio 0.07 > max 0.0" beside "hygiene
    cycle_ratio 0.07 violates max 0.0"). Only keys that produced a breach or an
    acknowledgement are suppressed: a key in both hygiene AND template that
    breaches only the TEMPLATE bound keeps its template violation as the sole
    reporter.
    """
    reported = {
        k for k in hygiene.keys if any(k in msg for msg in hygiene.breaches + hygiene.acknowledged)
    }
    return [v for v in violations if not any(v.startswith(k + " ") for k in reported)]


def score_v1(
    actual: PatternFingerprint,
    domain: DomainConfig,
    constraints: dict[str, tuple[str, float]],
    weights: dict[str, float],
    hygiene: HygieneOutcome,
    tolerance_eff: float,
) -> DriftReport:
    """V1 scoring: per-constraint weighted drift with CALLS-layer discount."""
    status: Status
    if not constraints:
        # Gate violations still force 'gate_failed' with no template constraints.
        status = "gate_failed" if hygiene.breaches else "clean"
        return DriftReport(
            domain=domain.name,
            fqn_prefix=domain.fqn_prefix,
            expected_pattern=domain.expected_pattern,
            actual=actual,
            ideal=_empty_ideal(domain),
            drift_score=0.0,
            violations=hygiene.breaches + hygiene.acknowledged,
            status=status,
            tolerance=tolerance_eff,
        )

    drift_sum, raw_violations, ideal_overrides = _weighted_constraint_drift(
        actual, constraints, weights
    )
    violations = _unreported(raw_violations, hygiene)
    status = "gate_failed" if hygiene.breaches else _classify(drift_sum, tolerance_eff)

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
        violations=violations + hygiene.breaches + hygiene.acknowledged,
        status=status,
        tolerance=tolerance_eff,
    )


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


def score_v2(
    actual: PatternFingerprint,
    domain: DomainConfig,
    gates: dict[str, tuple[str, float]],
    weights: dict[str, float],
    shape: tuple[tuple[tuple[float, ...], tuple[float, ...]], dict[str, float], tuple[float, ...]],
    hygiene: HygieneOutcome,
    tolerance_eff: float,
) -> DriftReport:
    """Fingerprint v2 drift: layered TV distance + hard gates (spec §3.3).

    ``shape`` is (ideal triads, layer weights, triad weights), all resolved by
    the catalog. Every constraint reaching this path is treated as a hard gate
    — the topological shape itself is measured by the TV terms, not
    constraints. Hygiene gate violations take precedence over score-driven
    classification (spec §2.2).
    """
    ideal, layers, triad_w = shape
    discount = clip_discount(actual)
    violations: list[str] = []

    tv_imp: float | None = None
    if actual.t_imports != ZERO_TRIADS:
        tv_imp, contribs = tv_distance(actual.t_imports, ideal[0], triad_w)
        violations.extend(_triad_violations("T_imports", actual.t_imports, ideal[0], contribs))
    tv_cal: float | None = None
    if actual.t_calls != ZERO_TRIADS:
        tv_cal, contribs = tv_distance(actual.t_calls, ideal[1], triad_w)
        violations.extend(_triad_violations("T_calls", actual.t_calls, ideal[1], contribs))

    gate_drift = 0.0
    raw_gate_violations: list[str] = []
    if gates:
        gate_drift, raw_gate_violations, _ = _weighted_constraint_drift(
            actual, gates, weights, discount=discount
        )
    violations.extend(_unreported(raw_gate_violations, hygiene))

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
    status: Status = "gate_failed" if hygiene.breaches else _classify(drift, tolerance_eff)

    return DriftReport(
        domain=domain.name,
        fqn_prefix=domain.fqn_prefix,
        expected_pattern=domain.expected_pattern,
        actual=actual,
        # The template's triad points; v1 fields zero.
        ideal=PatternFingerprint(
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
        ),
        drift_score=drift,
        violations=violations + hygiene.breaches + hygiene.acknowledged,
        status=status,
        tolerance=tolerance_eff,
        tv_imports=tv_imp,
        tv_calls=tv_cal,
    )
