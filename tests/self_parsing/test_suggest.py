"""Self-parsing validation for suggest-packages: the tool must catch the smell
it was built for (cgis.query) and read an already-nested package as aligned (#242)."""

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
    assert 0.38 <= report.modularity_q <= 0.48
    assert report.divergence == pytest.approx(1.0)


def test_cgis_guardian_nested_is_not_split(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """cgis.guardian has a real providers/ subpackage — a non-flat layout, so
    divergence is below 1.0 and the verdict is NOT 'split'. Calibrates that the
    0.2 divergence threshold does work on a nested package (spec: provisional)."""
    store, _, _ = root_graph_data
    report = suggest_packages(store.db_path, prefix="cgis.guardian", with_calls=False)
    assert report.verdict != "split"
