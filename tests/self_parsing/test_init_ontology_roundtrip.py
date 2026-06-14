"""Round-trip acceptance: propose on the cgis self-graph, drift passes green (#174)."""

from pathlib import Path

from cgis.core.models import Edge, Node
from cgis.query.drift.drift_service import DriftAnalysis, analyze_drift
from cgis.query.drift.ontology_init import propose_ontology
from cgis.storage.sqlite_store import SQLiteStore


def test_self_graph_proposal_round_trips(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
    tmp_path: Path,
) -> None:
    """propose_ontology → write yaml → analyze_drift → all green, no empty rows.

    Acceptance contract:
    - any_critical is False
    - every domain report has status != "empty"
    - every domain report satisfies drift_score <= tolerance + 1e-9
    - sanity: some discovered fqn_prefix contains "query"
    - sanity: some discovered fqn_prefix contains "extractors"
    """
    store, _, _ = root_graph_data
    db_path = store.db_path

    yaml_text = propose_ontology(db_path)
    out = tmp_path / "proposed.yaml"
    out.write_text(yaml_text, encoding="utf-8")

    analysis: DriftAnalysis = analyze_drift(db_path, str(out))

    assert analysis.any_critical is False, (
        "any_critical=True after round-trip; reports:\n"
        + "\n".join(
            f"  {r.fqn_prefix}: score={r.drift_score:.3f} tol={r.tolerance:.3f} status={r.status}"
            for r in analysis.reports
        )
    )

    for r in analysis.reports:
        assert r.status != "empty", (
            f"Domain '{r.fqn_prefix}' is 'empty' — fqn_prefix mismatch or empty subgraph."
        )
        assert r.drift_score <= r.tolerance + 1e-9, (
            f"Domain '{r.fqn_prefix}' drifted: {r.drift_score:.4f} > {r.tolerance:.4f} + ε"
        )

    discovered_prefixes = [r.fqn_prefix for r in analysis.reports]
    assert any("query" in p for p in discovered_prefixes), (
        f"No 'query' prefix found; discovered: {discovered_prefixes}"
    )
    assert any("extractors" in p for p in discovered_prefixes), (
        f"No 'extractors' prefix found; discovered: {discovered_prefixes}"
    )
