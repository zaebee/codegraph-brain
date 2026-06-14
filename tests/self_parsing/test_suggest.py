"""Self-parsing validation for suggest-packages: the tool must catch the smell
it was built for (cgis.query) and read an already-nested package as non-split (#242)."""

from cgis.core.models import Edge, Node
from cgis.query.analysis.suggest_service import suggest_packages
from cgis.storage.sqlite_store import SQLiteStore


def test_cgis_query_divergence_dropped_after_restructure(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Slice 2 split query/ into subpackages — cgis.query is no longer flat, so its
    divergence falls from the flat 1.0 toward alignment (measured ~0.31). Residual
    remains because the chosen by-responsibility layout (drift/analysis/context/
    render) is deliberately COARSER than the tool's by-connectivity communities
    (RFC §4: the algorithm suggests, the human groups). The tool therefore still
    nominally reports 'split'/'under_split' — it always pursues maximal community
    granularity — but the divergence drop is the measurable win the restructure
    bought."""
    store, _, _ = root_graph_data
    report = suggest_packages(store.db_path, prefix="cgis.query", with_calls=False)
    assert report.divergence < 0.5  # was 1.0 when flat; the restructure aligned the layout
    assert report.direction == "under_split"


def test_cgis_query_drift_subpkg_is_cohesive(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """The new query/drift/ subpackage is internally cohesive (low intra-package Q)
    → verdict 'leave': a sound boundary, not an arbitrary cut (#242 slice 2)."""
    store, _, _ = root_graph_data
    report = suggest_packages(store.db_path, prefix="cgis.query.drift", with_calls=False)
    assert report.verdict == "leave"
    assert report.modularity_q < 0.25


def test_cgis_guardian_nested_reads_below_flat(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """cgis.guardian has a real providers/ subpackage, so its divergence is
    BELOW the flat 1.0 — the nested layout carries some signal.

    HONESTY NOTE: guardian lands 'borderline' because its Q (~0.33) is under the
    split threshold, NOT because of low divergence (its D ~ 0.76 is still high).
    So this case does NOT exercise the D=0.2 aligned boundary — that boundary is
    covered end-to-end by test_suggest_aligned_nested_is_aligned (a synthetic
    nested-and-aligned package), since cgis has no genuinely well-aligned nested
    package in-repo. We pin the real numbers rather than a weak ``!= split``."""
    store, _, _ = root_graph_data
    report = suggest_packages(store.db_path, prefix="cgis.guardian", with_calls=False)
    assert report.verdict == "borderline"
    assert report.modularity_q < 0.35  # below split threshold → why it's not 'split'
    assert report.divergence < 1.0  # nested → below the flat-package degenerate 1.0
