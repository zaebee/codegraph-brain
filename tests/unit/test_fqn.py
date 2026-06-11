"""Unit tests for suffix-based FQN resolution."""

from pathlib import Path

from cgis.core.models import Node, NodeType
from cgis.query.fqn import resolve_fqn
from cgis.storage.sqlite_store import SQLiteStore


def _store(tmp_path: Path, ids: list[str]) -> SQLiteStore:
    """Open a store seeded with FUNCTION nodes for the given FQNs."""
    store = SQLiteStore(str(tmp_path / "fqn.db"))
    store.connect()
    nodes = [
        Node(
            id=i,
            type=NodeType.FUNCTION,
            name=i.rsplit(".", 1)[-1],
            file_path="f.py",
            start_line=1,
            end_line=2,
        )
        for i in ids
    ]
    store.save_graph(nodes, [])
    return store


def test_exact_match(tmp_path: Path) -> None:
    """Exact FQN resolves without suffix flag."""
    store = _store(tmp_path, ["a.b.fn", "c.fn"])
    res = resolve_fqn(store, "a.b.fn")
    assert res.resolved == "a.b.fn"
    assert res.via_suffix is False
    assert res.candidates == []
    store.disconnect()


def test_unique_suffix_resolves(tmp_path: Path) -> None:
    """A unique dot-boundary suffix resolves to the full FQN with via_suffix=True."""
    store = _store(tmp_path, ["src.cgis.query.triads.tv_distance"])
    res = resolve_fqn(store, "tv_distance")
    assert res.resolved == "src.cgis.query.triads.tv_distance"
    assert res.via_suffix is True
    store.disconnect()


def test_ambiguous_returns_candidates(tmp_path: Path) -> None:
    """Ambiguous suffix returns None resolved with all candidates listed."""
    store = _store(tmp_path, ["a.fn", "b.fn"])
    res = resolve_fqn(store, "fn")
    assert res.resolved is None
    assert res.candidates == ["a.fn", "b.fn"]
    store.disconnect()


def test_no_match(tmp_path: Path) -> None:
    """No matching node returns None resolved with empty candidates."""
    store = _store(tmp_path, ["a.fn"])
    res = resolve_fqn(store, "ghost")
    assert res.resolved is None
    assert res.candidates == []
    store.disconnect()
