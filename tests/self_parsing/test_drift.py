"""Self-drift guardrails: assert cgis and ui/src stay within declared drift tolerances.

The L3 counterpart of test_architecture.py — instead of checking individual
import edges, it scores each domain's graph topology against the ideal pattern
declared in docs/ontology/patterns.yaml. drift_tolerance is a ratchet:
exceeding it here means the structure regressed (or the ratchet must be
consciously re-negotiated in the YAML, with a comment).
"""

from cgis.core.models import Edge, Node
from cgis.query.drift import DomainConfig, DriftScorer
from cgis.query.fingerprint import FingerprintExtractor
from cgis.query.quotient import build_quotient
from cgis.storage.sqlite_store import SQLiteStore

from .conftest import ONTOLOGY_DIR, skip_if_no_ui

_PATTERNS = str(ONTOLOGY_DIR / "patterns.yaml")

_EXCEEDED = (
    "ARCHITECTURAL DRIFT — domain '{name}' ({prefix}) scored {score:.2f} "
    "against pattern '{pattern}' (tolerance {tol:.2f}).\nViolations:\n{violations}"
)


def _selected_domains(scorer: DriftScorer, graph: str) -> list[DomainConfig]:
    """Return the project domains measured under one profile: 'python' or 'typescript'."""
    return [d for d in scorer.load_project_domains() if d.profile == graph]


def _assert_within_tolerance(store: SQLiteStore, patterns_path: str, graph: str) -> None:
    """Score one graph's project domains and fail if any exceeds its declared tolerance."""
    scorer = DriftScorer(patterns_path)
    extractor = FingerprintExtractor(store)
    failures: list[str] = []

    for domain in _selected_domains(scorer, graph):
        fp = extractor.extract(domain.fqn_prefix)
        report = scorer.score(fp, domain)
        # Hygiene breaches (cycle_ratio, unresolved_ratio, tangle_ratio) force
        # gate_failed independently of drift_score, so the tolerance check below
        # would miss them. Assert the gate explicitly so a future hygiene
        # regression on the self-graph — e.g. a tangle_ratio rise (#186) — fails
        # CI rather than passing silently with weight 0 in the score.
        if report.status == "gate_failed":
            failures.append(
                f"[gate] {domain.name} ({domain.fqn_prefix}): hygiene gate_failed\n"
                + ("\n".join(f"  - {v}" for v in report.violations) or "  (none)")
            )
        if report.drift_score > domain.drift_tolerance:
            failures.append(
                _EXCEEDED.format(
                    name=domain.name,
                    prefix=domain.fqn_prefix,
                    score=report.drift_score,
                    pattern=domain.expected_pattern or "(hygiene)",
                    tol=domain.drift_tolerance,
                    violations="\n".join(f"  - {v}" for v in report.violations) or "  (none)",
                )
            )

    assert not failures, "\n\n".join(failures)


def _assert_domains_not_empty(store: SQLiteStore, patterns_path: str, graph: str) -> None:
    """Every selected domain must match at least one real node in the graph.

    Guards against silent all-zero fingerprints when FQN prefixes and ingest
    roots drift apart (e.g. ingesting src/cgis instead of src/ produces
    pipeline.* FQNs that no cgis.* prefix matches).
    """
    scorer = DriftScorer(patterns_path)
    all_ids = [n.id for n in store.get_all_nodes()]
    empty = [
        f"{d.name} ({d.fqn_prefix})"
        for d in _selected_domains(scorer, graph)
        if not any(i == d.fqn_prefix or i.startswith(d.fqn_prefix + ".") for i in all_ids)
    ]
    assert not empty, f"Declared domains match no nodes in the graph: {', '.join(empty)}"


# ---------------------------------------------------------------------------
# Python: src/ against patterns.yaml
# ---------------------------------------------------------------------------


def test_py_domains_match_nodes(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """All cgis.* domains declared in patterns.yaml exist in the self-parsed graph."""
    store, _, _ = root_graph_data
    _assert_domains_not_empty(store, _PATTERNS, graph="python")


def test_py_self_drift_within_tolerance(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Every cgis domain's drift score stays within its declared ratchet tolerance."""
    store, _, _ = root_graph_data
    _assert_within_tolerance(store, _PATTERNS, graph="python")


# ---------------------------------------------------------------------------
# TypeScript: ui/src against patterns.yaml
# ---------------------------------------------------------------------------


@skip_if_no_ui
def test_ts_domains_match_nodes(
    ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """All UI domains declared in patterns.yaml exist in the ui/src graph."""
    store, _, _ = ts_graph_data
    _assert_domains_not_empty(store, _PATTERNS, graph="typescript")


@skip_if_no_ui
def test_ts_self_drift_within_tolerance(
    ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Every UI domain's drift score stays within its declared ratchet tolerance."""
    store, _, _ = ts_graph_data
    _assert_within_tolerance(store, _PATTERNS, graph="typescript")


# ---------------------------------------------------------------------------
# Quotient (k=1) drift — OBSERVE-ONLY burn-in (spec §3.4)
# ---------------------------------------------------------------------------


def test_py_quotient_drift_observed_not_enforced(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Score the domain-quotient graph against project_level; report, never fail.

    enforce is false for the burn-in milestone: the assertion here is only
    that the machinery runs end-to-end and the binding stays observe-only.
    Known gap, recorded: quotient unresolved_ratio is 0 by construction
    (raw_call targets belong to no domain), so the k=1 CALLS layer is
    undiscounted until enforcement flips.
    """
    store, _, _ = root_graph_data
    scorer = DriftScorer(_PATTERNS)
    bindings = scorer.load_project_level()
    assert bindings, "project_level binding missing from patterns.yaml"

    domains = _selected_domains(scorer, graph="python")
    qnodes, qedges = build_quotient(store.get_all_nodes(), store.get_all_edges(), domains)
    extractor = FingerprintExtractor.from_graph(qnodes, qedges)

    for binding in bindings:
        assert binding.enforce is False, (
            "project_level flipped to enforce: true — this test must become a "
            "tolerance assertion like _assert_within_tolerance (see spec §3.4)"
        )
        report = scorer.score(extractor.extract(binding.fqn_prefix), binding)
        print(
            f"\n[quotient observe-only] {binding.name}: drift={report.drift_score:.3f} "
            f"(tolerance {binding.drift_tolerance:.2f}, tv_imports={report.tv_imports}, "
            f"tv_calls={report.tv_calls})\n  violations: {report.violations}"
        )
