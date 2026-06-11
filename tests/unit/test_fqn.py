"""Unit tests for suffix-based FQN resolution."""

from pathlib import Path

from cgis.core.models import Node, NodeType
from cgis.query.fqn import resolve_fqn
from cgis.storage.sqlite_store import SQLiteStore


def _seed_store(db_path: Path, ids: list[str]) -> Path:
    """Seed a SQLite store with FUNCTION nodes for the given FQNs and return the db path."""
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
    with SQLiteStore(str(db_path)) as store:
        store.save_graph(nodes, [])
    return db_path


def test_exact_match(tmp_path: Path) -> None:
    """Exact FQN resolves without suffix flag."""
    db = _seed_store(tmp_path / "fqn.db", ["a.b.fn", "c.fn"])
    with SQLiteStore(str(db)) as store:
        res = resolve_fqn(store, "a.b.fn")
    assert res.resolved == "a.b.fn"
    assert res.via_suffix is False
    assert res.candidates == []
    assert res.truncated is False


def test_unique_suffix_resolves(tmp_path: Path) -> None:
    """A unique dot-boundary suffix resolves to the full FQN with via_suffix=True."""
    db = _seed_store(tmp_path / "fqn.db", ["src.cgis.query.triads.tv_distance"])
    with SQLiteStore(str(db)) as store:
        res = resolve_fqn(store, "tv_distance")
    assert res.resolved == "src.cgis.query.triads.tv_distance"
    assert res.via_suffix is True
    assert res.truncated is False


def test_ambiguous_returns_candidates(tmp_path: Path) -> None:
    """Ambiguous suffix returns None resolved with all candidates listed, truncated False."""
    db = _seed_store(tmp_path / "fqn.db", ["a.fn", "b.fn"])
    with SQLiteStore(str(db)) as store:
        res = resolve_fqn(store, "fn")
    assert res.resolved is None
    assert res.candidates == ["a.fn", "b.fn"]
    assert res.truncated is False


def test_no_match(tmp_path: Path) -> None:
    """No matching node returns None resolved with empty candidates."""
    db = _seed_store(tmp_path / "fqn.db", ["a.fn"])
    with SQLiteStore(str(db)) as store:
        res = resolve_fqn(store, "ghost")
    assert res.resolved is None
    assert res.candidates == []
    assert res.truncated is False


def test_truncation_signal(tmp_path: Path) -> None:
    """12 matching nodes yield 10 candidates and truncated=True."""
    ids = [f"mod{i}.go" for i in range(12)]
    db = _seed_store(tmp_path / "fqn.db", ids)
    with SQLiteStore(str(db)) as store:
        res = resolve_fqn(store, "go")
    assert res.resolved is None
    assert len(res.candidates) == 10
    assert res.truncated is True
    # Candidates are a prefix of the sorted list; none start with "raw_call:"
    for c in res.candidates:
        assert "go" in c
