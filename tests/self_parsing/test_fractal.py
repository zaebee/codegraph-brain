"""cgis measured on itself — acceptance criterion 1 of the #186 fractal spec."""

from cgis.core.models import Edge, EdgeType, Node
from cgis.query.drift.fractal import analyze_fractal
from cgis.storage.sqlite_store import SQLiteStore


def test_cgis_calls_ladder_is_hierarchical(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Coarsening cgis's own call graph must ADD motif diversity.

    The sign is the contract; the band is only a sanity rail, deliberately
    loose so ordinary refactors do not break it. The spec's baseline measured
    +0.146 on a ``src/`` ingest with ``--source-root src``. This fixture ingests
    ``src/cgis/`` — one directory level shallower — which collapses the whole
    package into 13 groups in a single step at ``T3_up1``, so the same repo
    reads steeper here: **+0.305** (R² 0.90, SE 0.073, 4 live rungs).

    That the same code yields two different slopes under two ingest roots is the
    metric behaving correctly, not a defect: the ladder is built from the
    repository's own directory structure, and the ingest root is part of that
    structure. What must NOT change is the sign.
    """
    _store, nodes, edges = graph_data

    report = analyze_fractal(nodes, edges, EdgeType.CALLS)

    assert report.verdict == "hierarchical"
    assert report.fit is not None
    assert 0.05 <= report.fit.slope <= 0.45


def test_cgis_ladder_reports_a_real_curve(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """The report must carry the evidence, not only the headline."""
    _store, nodes, edges = graph_data

    report = analyze_fractal(nodes, edges, EdgeType.CALLS)

    assert len(report.rungs) >= 4
    assert report.rungs[0].name == "T0_symbol"
    assert sum(1 for r in report.rungs if r.live) >= 3
    assert all(r.entropy is None or r.entropy >= 0.0 for r in report.rungs)
