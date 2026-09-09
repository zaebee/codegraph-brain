"""Unit tests for the graph-freshness value objects (#175)."""

from cgis.core.freshness import Freshness, FreshnessState


def test_fresh_reports_nothing_to_say() -> None:
    """A fresh graph carries no counts and no reason — nothing to print."""
    f = Freshness(state=FreshnessState.FRESH)
    assert f.changed == 0
    assert f.missing == 0
    assert f.reason is None


def test_unknown_carries_its_reason() -> None:
    """`UNKNOWN` is not `STALE`: the reader is told *why* it cannot be checked.

    Two causes need different remedies — a graph older than the table wants a
    re-ingest, a moved root wants an explicit root — so the reason travels with it.
    """
    f = Freshness(state=FreshnessState.UNKNOWN, reason="graph predates the ingest_state table")
    assert f.state is FreshnessState.UNKNOWN
    assert "predates" in (f.reason or "")
