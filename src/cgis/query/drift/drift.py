"""DriftScorer: scores PatternFingerprints against the templates in patterns.yaml.

Since #214 the scorer is the facade over two parts: `PatternCatalog`
(`catalog.py`), which loads and resolves the ontology, and the scoring math
(`scoring.py`), which compares one fingerprint with what the catalog resolved.
`DomainConfig`, `DriftReport` and `FitQuality` are re-exported from here, where
callers have always imported them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cgis.query.drift.catalog import DomainConfig, PatternCatalog
from cgis.query.drift.scoring import (
    DriftReport,
    FitQuality,
    HygieneOutcome,
    apply_baseline,
    clip_discount,
    hygiene_check,
    score_v1,
    score_v2,
    signal_report,
)

if TYPE_CHECKING:
    from cgis.query.drift.fingerprint import PatternFingerprint

__all__ = ["DomainConfig", "DriftReport", "DriftScorer", "FitQuality"]


def _fit_config(domain: str, template: str, profile: str) -> DomainConfig:
    """Synthetic domain binding used to measure distance to one template."""
    return DomainConfig(
        name=domain,
        fqn_prefix=domain,
        expected_pattern=template,
        profile=profile,
        drift_tolerance=1.0,
    )


class DriftScorer:
    """Load patterns.yaml and score actual PatternFingerprints against ideal templates.

    Global hygiene invariants apply to every domain (template constraints win per
    component); CALLS-derived component weights are discounted by
    (1 - unresolved_ratio) before renormalization.
    """

    def __init__(self, patterns_config: str) -> None:
        """Load the patterns YAML file at patterns_config path into a catalog."""
        self.catalog = PatternCatalog(patterns_config)

    def load_project_domains(self) -> list[DomainConfig]:
        """Return all project domains declared in patterns.yaml."""
        return self.catalog.load_project_domains()

    def load_project_level(self) -> list[DomainConfig]:
        """Return project-level quotient bindings (spec §3.4); enforce defaults False here."""
        return self.catalog.load_project_level()

    def ideal_for(self, pattern_name: str) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
        """A template's (ideal_imports, ideal_calls) triads, or None; see PatternCatalog."""
        return self.catalog.ideal_for(pattern_name)

    def layers_for(self, profile_name: str) -> dict[str, float] | None:
        """A profile's validated layer weights, or None; see PatternCatalog."""
        return self.catalog.layers_for(profile_name)

    def triad_weights_for(self, profile_name: str) -> tuple[float, ...]:
        """A profile's per-triad weights; see PatternCatalog."""
        return self.catalog.triad_weights_for(profile_name)

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
            return signal_report(actual, domain, status="empty", tolerance_eff=tolerance_eff)
        if actual.edge_count == 0:
            return signal_report(actual, domain, status="no_signal", tolerance_eff=tolerance_eff)
        catalog = self.catalog
        hygiene = catalog.hygiene
        # SCORE math unchanged: the merged dict feeds the v1/v2 paths.
        constraints = {**hygiene, **catalog.template_constraints(domain)}

        # STATUS: hygiene evaluated separately with baseline-relaxed effective bounds.
        breaches, acknowledged = hygiene_check(
            actual, apply_baseline(hygiene, domain.hygiene_baseline), domain
        )
        outcome = HygieneOutcome(frozenset(hygiene), breaches, acknowledged)

        ideal = (
            None if domain.expected_pattern is None else catalog.ideal_for(domain.expected_pattern)
        )
        layers = None if domain.profile is None else catalog.layers_for(domain.profile)
        weights = catalog.weights_for(domain)
        if ideal is None or layers is None or domain.profile is None:
            return score_v1(actual, domain, constraints, weights, outcome, tolerance_eff)
        shape = (ideal, layers, catalog.triad_weights_for(domain.profile))
        return score_v2(actual, domain, constraints, weights, shape, outcome, tolerance_eff)

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
                self.score(actual, _fit_config(actual.domain, t_name, profile)).drift_score,
            )
            for t_name in self.catalog.patterns
        ]
        fits.sort(key=lambda pair: (pair[1], pair[0]))
        return fits

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
        report = self.score(actual, _fit_config(actual.domain, template, profile))
        layers = self.catalog.layers_for(profile) or {}
        discount = clip_discount(actual)
        imp_w = layers.get("imports", 0.0) if report.tv_imports is not None else 0.0
        cal_w = layers.get("calls", 0.0) * discount if report.tv_calls is not None else 0.0
        total = imp_w + cal_w
        if total <= 0.0:
            return None
        return (imp_w * (report.tv_imports or 0.0) + cal_w * (report.tv_calls or 0.0)) / total
