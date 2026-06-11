"""Drift-analysis orchestration shared by the CLI and the MCP server."""

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


def analyze_drift(db_path: str, patterns_path: str, max_drift: float = 0.50) -> DriftAnalysis:
    """Score every project domain (and the quotient level) against patterns.

    Raises on unreadable inputs — callers translate errors to their medium.
    ``any_critical`` counts quotient bindings only when they are enforced.

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

    reports: list[DriftReport] = []
    quotient: list[tuple[DomainConfig, DriftReport]] = []
    with SQLiteStore(db_path) as store:
        extractor = FingerprintExtractor(store)
        reports.extend(
            scorer.score(extractor.extract(domain.fqn_prefix), domain) for domain in domains
        )
        level_bindings = scorer.load_project_level()
        if level_bindings:
            qnodes, qedges = build_quotient(store.get_all_nodes(), store.get_all_edges(), domains)
            q_extractor = FingerprintExtractor.from_graph(qnodes, qedges)
            quotient = [
                (b, scorer.score(q_extractor.extract(b.fqn_prefix), b)) for b in level_bindings
            ]

    any_critical = any(r.drift_score >= max_drift for r in reports) or any(
        r.drift_score >= max_drift for b, r in quotient if b.enforce
    )
    return DriftAnalysis(reports=reports, quotient=quotient, any_critical=any_critical)
