"""Drift-analysis orchestration shared by the CLI and the MCP server."""

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from cgis.query.drift import DomainConfig, DriftReport, DriftScorer
from cgis.query.fingerprint import FingerprintExtractor
from cgis.query.quotient import build_quotient
from cgis.storage.sqlite_store import SQLiteStore


@dataclass(frozen=True)
class DriftAnalysis:
    """Full drift run: per-domain reports plus the quotient (k=1) layer."""

    reports: list[DriftReport]
    quotient: list[tuple[DomainConfig, DriftReport]]
    any_critical: bool


_ALLOWED_PATTERN_SUFFIXES = {".yaml", ".yml"}


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
) -> DriftAnalysis:
    """Score every project domain (and the quotient level) against patterns.

    When ``profile`` is set, only domains (and project_level bindings) whose
    ``binding.profile == profile`` (or ``binding.profile is None``) are scored.
    Profile-less domains match any filter — they carry language-agnostic hygiene
    rules that should apply regardless of which language graph is being measured.
    Omitting ``profile`` retains the score-everything default.

    ``any_critical`` is True when:
    - any domain report's ``drift_score >= max_drift`` (enforce-blind, pre-existing
      semantics — full precedence rework is #170, deliberately untouched here), OR
    - any ENFORCED project domain has status ``"empty"`` (spec §2.3): a broken
      ``fqn_prefix`` ontology must not silently pass CI; ``enforce: false`` stays
      observe-only, OR
    - any ENFORCED quotient binding exceeds ``max_drift`` or has status ``"empty"``.
    ``"no_signal"`` never trips the gate.

    For each ``"empty"`` report, the ``note`` field is decorated with closest-prefix
    suggestions from the suffix index (spec §2.4).

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
        reports = [scorer.score(extractor.extract(domain.fqn_prefix), domain) for domain in domains]
        reports = [
            dataclasses.replace(r, note=_empty_note(store, r.fqn_prefix))
            if r.status == "empty"
            else r
            for r in reports
        ]
        level_bindings = scorer.load_project_level()
        if profile is not None:
            level_bindings = [
                b for b in level_bindings if b.profile is None or b.profile == profile
            ]
        if level_bindings:
            qnodes, qedges = build_quotient(store.get_all_nodes(), store.get_all_edges(), domains)
            q_extractor = FingerprintExtractor.from_graph(qnodes, qedges)
            quotient = [
                (b, scorer.score(q_extractor.extract(b.fqn_prefix), b)) for b in level_bindings
            ]
            quotient = [
                (b, dataclasses.replace(r, note=_empty_note(store, r.fqn_prefix)))
                if r.status == "empty"
                else (b, r)
                for b, r in quotient
            ]

    # "empty" on an ENFORCED binding (project domain or quotient) is always
    # critical regardless of score (spec §2.3): a broken fqn_prefix ontology
    # must not silently pass CI. enforce: false stays observe-only. The
    # score term below is enforce-blind for project domains — pre-existing
    # semantics, deliberately untouched here (#170). "no_signal" never trips.
    any_critical = (
        any(r.drift_score >= max_drift for r in reports)
        or any(r.status == "empty" for d, r in zip(domains, reports, strict=True) if d.enforce)
        or any(
            (r.drift_score >= max_drift or r.status == "empty") for b, r in quotient if b.enforce
        )
    )
    return DriftAnalysis(reports=reports, quotient=quotient, any_critical=any_critical)
