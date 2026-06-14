"""Self-parsing validation for suggest-packages: the tool must catch the smell
it was built for (cgis.query) and read an already-nested package as non-split (#242)."""

import pytest

from cgis.core.models import Edge, Node
from cgis.query.suggest_service import suggest_packages
from cgis.storage.sqlite_store import SQLiteStore


def test_cgis_query_is_flagged_split(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """cgis.query (19 flat files, Q~0.43) must read as 'split' — the canonical
    dogfood validation (the self-drift analogue, #242)."""
    store, _, _ = root_graph_data
    report = suggest_packages(store.db_path, prefix="cgis.query", with_calls=False)
    assert report.verdict == "split"
    assert report.direction == "under_split"
    # Flat package → D == 1.0 always; the verdict rides on Q. Band kept wide
    # because Q creeps up as query/ grows (cohesion/suggest_service lifted it to
    # ~0.475); slice 2's restructure will reset this domain anyway.
    assert 0.38 <= report.modularity_q <= 0.52
    assert report.divergence == pytest.approx(1.0)


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
