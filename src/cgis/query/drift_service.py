"""Drift-analysis orchestration shared by the CLI and the MCP server."""

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from cgis.core.models import Node
from cgis.query.drift import DomainConfig, DriftReport, DriftScorer, FitQuality
from cgis.query.fingerprint import FingerprintExtractor
from cgis.query.ontology_init import discover_domains
from cgis.query.quotient import build_quotient
from cgis.storage.sqlite_store import SQLiteStore


@dataclass(frozen=True)
class DriftAnalysis:
    """Full drift run: per-domain reports plus the quotient (k=1) layer."""

    reports: list[DriftReport]
    quotient: list[tuple[DomainConfig, DriftReport]]
    any_critical: bool
    # Candidate domain prefixes present in the graph but bound by no project_domain
    # — code drift silently ignores (#177 coverage roll-up).
    coverage: list[str] = field(default_factory=list)


_ALLOWED_PATTERN_SUFFIXES = {".yaml", ".yml"}

# Fit-quality bands (#177): residual ≤ _GOOD_RESIDUAL is a clean archetype match;
# residual > max_residual means no template fits (see _fit_quality).
_GOOD_RESIDUAL = 0.25
# Statuses whose fingerprint carries a real, fittable shape.
_FITTABLE_STATUSES = frozenset({"clean", "warning", "critical", "gate_failed"})


def _fit_quality(
    scorer: DriftScorer, report: DriftReport, profile: str, max_residual: float
) -> FitQuality | None:
    """Rank the alphabet against one domain's shape and band the closest fit (#177).

    Ranking uses the canonical distance (``fit_templates``); the reported
    residuals and band use the gate-FREE ``shape_residual`` so a gate breach
    (e.g. a cycle) never masquerades as "no template fits". Returns None when
    the alphabet declares no templates (a profiles-only config).
    """
    fits = scorer.fit_templates(report.actual, profile)
    if not fits:
        return None
    nearest_name = fits[0][0]
    runner_name = fits[1][0] if len(fits) > 1 else None
    nearest_res = scorer.shape_residual(report.actual, profile, nearest_name)
    runner_res = scorer.shape_residual(report.actual, profile, runner_name) if runner_name else None
    # Keep the good-band reachable even when --max-residual is set below it.
    good_cut = min(_GOOD_RESIDUAL, max_residual)
    band: Literal["good", "weak", "none"]
    if nearest_res > max_residual:
        band = "none"
    elif nearest_res <= good_cut:
        band = "good"
    else:
        band = "weak"
    return FitQuality(
        nearest_template=nearest_name,
        nearest_residual=nearest_res,
        runner_up_template=runner_name,
        runner_up_residual=runner_res,
        band=band,
    )


def _uncovered_prefixes(nodes: list[Node], domain_prefixes: list[str]) -> list[str]:
    """Discovered domain candidates that overlap no declared domain prefix (#177).

    A candidate is "covered" when it shares a subtree with a declared domain
    (either is an ancestor-or-equal of the other). What remains is graph code
    no ``project_domain`` binds — exactly what drift silently skips.
    """
    return sorted(
        candidate
        for candidate in discover_domains(nodes)
        if not any(
            candidate == d or candidate.startswith(f"{d}.") or d.startswith(f"{candidate}.")
            for d in domain_prefixes
        )
    )


def _empty_note(store: SQLiteStore, fqn_prefix: str) -> str:
    """Return a 'matched 0 nodes' diagnostic with closest-prefix suggestions (spec §2.4).

    Short-circuits on blank prefixes (no DB query). Tries the full prefix as a
    dot-boundary suffix first, then its last segment; caps at 3 suggestions.
    """
    base = f"fqn_prefix '{fqn_prefix}' matched 0 nodes"
    if not fqn_prefix.strip():
        return base
    matches = store.find_nodes_by_suffix(fqn_prefix, limit=3)
    if not matches:
        last_segment = fqn_prefix.rsplit(".", maxsplit=1)[-1]
        if last_segment.strip():
            matches = store.find_nodes_by_suffix(last_segment, limit=3)
    if not matches:
        return base
    ids = sorted(n.id for n in matches)[:3]
    return f"{base}; did you mean: {', '.join(ids)}?"


def analyze_drift(
    db_path: str,
    patterns_path: str,
    max_drift: float = 0.50,
    profile: str | None = None,
    max_residual: float = 0.45,
) -> DriftAnalysis:
    """Score every project domain (and the quotient level) against patterns.

    When ``profile`` is set, only domains (and project_level bindings) whose
    ``binding.profile == profile`` (or ``binding.profile is None``) are scored.
    Profile-less domains match any filter — they carry language-agnostic hygiene
    rules that should apply regardless of which language graph is being measured.
    Omitting ``profile`` retains the score-everything default.

    ``any_critical`` is True when any ENFORCED binding has status in
    ``{"critical", "gate_failed", "empty"}`` (spec §2.3, #170B).  The
    per-domain ``drift_tolerance`` determines whether a score yields
    ``"critical"``; ``max_drift`` is the fallback default tolerance for domains
    that do not declare their own (``drift_tolerance`` is no longer a global cap).
    ``"no_signal"`` never trips the gate.  ``enforce: false`` stays observe-only.

    For each ``"empty"`` report, the ``note`` field is decorated with closest-prefix
    suggestions from the suffix index (spec §2.4).

    Each profiled report with a real shape also carries ``fit`` (#177): the nearest
    alphabet template + residual, the runner-up, and a ``good|weak|none`` band
    (``none`` when the nearest residual exceeds ``max_residual`` — no archetype
    captures the domain). ``DriftAnalysis.coverage`` lists graph prefixes bound by
    no project_domain (code drift silently ignores).

    Raises:
        FileNotFoundError: If ``db_path`` does not point to an existing file.
            Use ``cgis ingest`` to create the graph database first.
        ValueError: If ``patterns_path`` does not end in ``.yaml`` or ``.yml``.
        FileNotFoundError: If ``patterns_path`` does not point to an existing file.
    """
    if not Path(db_path).is_file():
        msg = f"Graph database not found: {db_path}"
        raise FileNotFoundError(msg)
    resolved = Path(patterns_path).resolve()
    if resolved.suffix.lower() not in _ALLOWED_PATTERN_SUFFIXES:
        msg = f"patterns_path must be a .yaml or .yml file, got: {patterns_path!r}"
        raise ValueError(msg)
    if not resolved.is_file():
        msg = f"Patterns file not found: {patterns_path}"
        raise FileNotFoundError(msg)
    scorer = DriftScorer(str(resolved))
    domains = scorer.load_project_domains()
    if profile is not None:
        domains = [d for d in domains if d.profile is None or d.profile == profile]

    quotient: list[tuple[DomainConfig, DriftReport]] = []
    with SQLiteStore(db_path) as store:
        extractor = FingerprintExtractor(store)
        reports = [
            scorer.score(extractor.extract(domain.fqn_prefix), domain, default_tolerance=max_drift)
            for domain in domains
        ]
        reports = [
            dataclasses.replace(r, note=_empty_note(store, r.fqn_prefix))
            if r.status == "empty"
            else r
            for r in reports
        ]
        # Annotate fit-quality for domains with a real shape and a profile (#177).
        reports = [
            dataclasses.replace(r, fit=_fit_quality(scorer, r, d.profile, max_residual))
            if d.profile is not None and r.status in _FITTABLE_STATUSES
            else r
            for d, r in zip(domains, reports, strict=True)
        ]
        all_nodes = store.get_all_nodes()  # one materialization, reused below (#177 #4)
        coverage = _uncovered_prefixes(all_nodes, [d.fqn_prefix for d in domains])
        level_bindings = scorer.load_project_level()
        if profile is not None:
            level_bindings = [
                b for b in level_bindings if b.profile is None or b.profile == profile
            ]
        if level_bindings:
            qnodes, qedges = build_quotient(all_nodes, store.get_all_edges(), domains)
            q_extractor = FingerprintExtractor.from_graph(qnodes, qedges)
            quotient = [
                (b, scorer.score(q_extractor.extract(b.fqn_prefix), b, default_tolerance=max_drift))
                for b in level_bindings
            ]
            quotient = [
                (b, dataclasses.replace(r, note=_empty_note(store, r.fqn_prefix)))
                if r.status == "empty"
                else (b, r)
                for b, r in quotient
            ]

    # Gate is now uniformly status-based and enforce-respecting (spec §2.3,
    # #170B): 'critical' and 'gate_failed' on enforced bindings trip any_critical;
    # 'empty' on enforced bindings also trips it (broken fqn_prefix must not
    # silently pass CI). 'no_signal' never trips. enforce:false stays observe-only.
    any_critical = any(
        r.status in ("critical", "gate_failed", "empty")
        for d, r in zip(domains, reports, strict=True)
        if d.enforce
    ) or any(r.status in ("critical", "gate_failed", "empty") for b, r in quotient if b.enforce)
    return DriftAnalysis(
        reports=reports, quotient=quotient, any_critical=any_critical, coverage=coverage
    )
