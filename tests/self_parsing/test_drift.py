"""Self-drift guardrails: assert cgis and ui/src stay within declared drift tolerances.

The L3 counterpart of test_architecture.py — instead of checking individual
import edges, it scores each domain's graph topology against the ideal pattern
declared in docs/ontology/patterns.yaml. drift_tolerance is a ratchet:
exceeding it here means the structure regressed (or the ratchet must be
consciously re-negotiated in the YAML, with a comment).
"""

import dataclasses

from cgis.core.models import Edge, Node
from cgis.query.drift.drift import DomainConfig, DriftScorer
from cgis.query.drift.drift_service import _uncovered_prefixes
from cgis.query.drift.fingerprint import FingerprintExtractor
from cgis.query.drift.quotient import QUOTIENT_PREFIX, build_quotient, quotient_call_tally
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


def test_py_every_package_is_bound_to_a_domain(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """No cgis code sits outside every domain, where drift would silently skip it.

    The self-audit found cgis.bench, cgis.import_names and cgis.__main__ bound
    by nothing. The one exemption is what cgis/__init__.py defines itself
    (log_to_stderr): a prefix covering the package root would cover everything.
    """
    _, nodes, _ = root_graph_data
    scorer = DriftScorer(_PATTERNS)
    uncovered = _uncovered_prefixes(
        nodes, [d.fqn_prefix for d in _selected_domains(scorer, "python")]
    )
    package_root = {n.id for n in nodes if n.file_path.endswith("cgis/__init__.py")}
    assert [p for p in uncovered if p not in package_root] == []


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


_PLANES = {
    "build": (
        (
            "pipeline",
            "languages",
            "languages.python",
            "languages.typescript",
            "languages.go",
            "resolution",
            "storage",
        ),
        "orchestrator",
    ),
    "read": (("storage", "query", "guardian", "cli", "api"), "layered_dag"),
}


def test_plane_quotients_observed_not_enforced(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Score each plane's quotient against its template; print, never fail (spec: Drift gates).

    The enforced topology gate is the forbidden-edge test in test_architecture.py.
    These numbers are kept for the record and for #554.
    """
    store, _, _ = root_graph_data
    scorer = DriftScorer(_PATTERNS)
    # The plane report owns its binding: the project_level one it borrowed was
    # retired in step 6 (#146). expected_pattern is set per plane below.
    binding = DomainConfig(
        name="plane", fqn_prefix=QUOTIENT_PREFIX, expected_pattern=None, profile="python",
        drift_tolerance=1.0, enforce=False,
    )  # fmt: skip
    by_name = {d.name: d for d in _selected_domains(scorer, graph="python")}
    # The registry module `cgis.languages` is in no drift domain (a domain on its
    # prefix would take the packs too); for the plane it is a node of its own, the
    # longest-prefix match leaving it only the registry's nodes.
    by_name["languages"] = DomainConfig(
        name="languages", fqn_prefix="cgis.languages", expected_pattern=None
    )
    nodes, edges = store.get_all_nodes(), store.get_all_edges()
    for plane, (names, template) in _PLANES.items():
        domains = [by_name[n] for n in names]
        qnodes, qedges = build_quotient(nodes, edges, domains)
        extractor = FingerprintExtractor.from_graph(
            qnodes, qedges, quotient_call_tally(nodes, edges, domains)
        )
        fingerprint = extractor.extract(binding.fqn_prefix)
        assert fingerprint.node_count == len(domains), f"{plane}: a domain matched nothing"
        # A domain with no edge in its plane is a hole, not a better score: step 6
        # left the packs isolated (their only link, the registry, was in no domain)
        # and the build plane read 0.016 instead of an honest number.
        linked = {e.source for e in qedges} | {e.target for e in qedges}
        isolated = sorted(n.id for n in qnodes if n.id not in linked)
        assert not isolated, f"{plane}: quotient nodes with no edge: {isolated}"
        report = scorer.score(fingerprint, dataclasses.replace(binding, expected_pattern=template))
        print(f"\n[plane observe-only] {plane} vs {template}: drift={report.drift_score:.3f}")
