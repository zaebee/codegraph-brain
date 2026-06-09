"""Self-drift guardrails: assert cgis and ui/src stay within declared drift tolerances.

The L3 counterpart of test_architecture.py — instead of checking individual
import edges, it scores each domain's graph topology against the ideal pattern
declared in docs/ontology/patterns*.yaml. drift_tolerance is a ratchet:
exceeding it here means the structure regressed (or the ratchet must be
consciously re-negotiated in the YAML, with a comment).
"""

from cgis.core.models import Edge, Node
from cgis.query.drift import DriftScorer
from cgis.query.fingerprint import FingerprintExtractor
from cgis.storage.sqlite_store import SQLiteStore

from .conftest import ONTOLOGY_DIR, skip_if_no_ui

_PATTERNS = str(ONTOLOGY_DIR / "patterns.yaml")
_PATTERNS_UI = str(ONTOLOGY_DIR / "patterns_ui.yaml")

_EXCEEDED = (
    "ARCHITECTURAL DRIFT — domain '{name}' ({prefix}) scored {score:.2f} "
    "against pattern '{pattern}' (tolerance {tol:.2f}).\nViolations:\n{violations}"
)


def _assert_within_tolerance(store: SQLiteStore, patterns_path: str) -> None:
    """Score every project domain and fail if any exceeds its declared tolerance."""
    scorer = DriftScorer(patterns_path)
    extractor = FingerprintExtractor(store)
    failures: list[str] = []

    for domain in scorer.load_project_domains():
        fp = extractor.extract(domain.fqn_prefix)
        report = scorer.score(fp, domain)
        if report.drift_score > domain.drift_tolerance:
            failures.append(
                _EXCEEDED.format(
                    name=domain.name,
                    prefix=domain.fqn_prefix,
                    score=report.drift_score,
                    pattern=domain.expected_pattern,
                    tol=domain.drift_tolerance,
                    violations="\n".join(f"  - {v}" for v in report.violations) or "  (none)",
                )
            )

    assert not failures, "\n\n".join(failures)


def _assert_domains_not_empty(store: SQLiteStore, patterns_path: str) -> None:
    """Every declared domain must match at least one real node in the graph.

    Guards against silent all-zero fingerprints when FQN prefixes and ingest
    roots drift apart (e.g. ingesting src/cgis instead of src/ produces
    pipeline.* FQNs that no cgis.* prefix matches).
    """
    scorer = DriftScorer(patterns_path)
    all_ids = [n.id for n in store.get_all_nodes()]
    empty = [
        f"{d.name} ({d.fqn_prefix})"
        for d in scorer.load_project_domains()
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
    _assert_domains_not_empty(store, _PATTERNS)


def test_py_self_drift_within_tolerance(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Every cgis domain's drift score stays within its declared ratchet tolerance."""
    store, _, _ = root_graph_data
    _assert_within_tolerance(store, _PATTERNS)


# ---------------------------------------------------------------------------
# TypeScript: ui/src against patterns_ui.yaml
# ---------------------------------------------------------------------------


@skip_if_no_ui
def test_ts_domains_match_nodes(
    ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """All UI domains declared in patterns_ui.yaml exist in the ui/src graph."""
    store, _, _ = ts_graph_data
    _assert_domains_not_empty(store, _PATTERNS_UI)


@skip_if_no_ui
def test_ts_self_drift_within_tolerance(
    ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Every UI domain's drift score stays within its declared ratchet tolerance."""
    store, _, _ = ts_graph_data
    _assert_within_tolerance(store, _PATTERNS_UI)
