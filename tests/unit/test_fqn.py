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
    db = _seed_store(tmp_path / "fqn.db", ["src.cgis.query.drift.triads.tv_distance"])
    with SQLiteStore(str(db)) as store:
        res = resolve_fqn(store, "tv_distance")
    assert res.resolved == "src.cgis.query.drift.triads.tv_distance"
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


def test_empty_fqn_returns_unresolved(tmp_path: Path) -> None:
    """Empty or whitespace-only FQN short-circuits without touching the store."""
    db = _seed_store(tmp_path / "fqn.db", ["a.fn"])
    with SQLiteStore(str(db)) as store:
        res_empty = resolve_fqn(store, "")
        res_blank = resolve_fqn(store, "  ")
    assert res_empty.resolved is None
    assert res_empty.candidates == []
    assert res_blank.resolved is None
    assert res_blank.candidates == []


def test_surrounding_whitespace_is_stripped(tmp_path: Path) -> None:
    """Leading/trailing whitespace must not break exact or suffix lookups."""
    db = _seed_store(tmp_path / "fqn.db", ["a.b.fn"])
    with SQLiteStore(db) as store:
        assert resolve_fqn(store, " a.b.fn ").resolved == "a.b.fn"
        assert resolve_fqn(store, "\tfn\n").resolved == "a.b.fn"


def _ingested_below(tmp_path: Path, ids: list[str], *dirs: str, source: str = "") -> Path:
    """A graph whose recorded ingest root is ``tmp_path/<dirs...>`` (#568)."""
    root = tmp_path.joinpath(*dirs)
    root.mkdir(parents=True)
    db = _seed_store(tmp_path / "fqn.db", ids)
    with SQLiteStore(str(db)) as store:
        store.record_ingest(str(root))
        if source:
            store.record_ingest_options([source], None)
    return db


def test_a_directory_above_the_graph_is_dropped(tmp_path: Path) -> None:
    """The A/B agent's first call: a name built from the path, one segment too long."""
    db = _ingested_below(tmp_path, ["domains.service.credit_wallet"], "backend", "app")
    with SQLiteStore(str(db)) as store:
        one = resolve_fqn(store, "app.domains.service.credit_wallet")
        two = resolve_fqn(store, "backend.app.domains.service.credit_wallet")

    assert one.resolved == two.resolved == "domains.service.credit_wallet"
    assert one.via_suffix, "the answer must say what the name resolved to"


def test_a_source_root_is_dropped_too(tmp_path: Path) -> None:
    db = _ingested_below(tmp_path, ["cgis.pipeline.run"], "repo", source="src")
    with SQLiteStore(str(db)) as store:
        assert resolve_fqn(store, "src.cgis.pipeline.run").resolved == "cgis.pipeline.run"


def test_a_segment_that_is_not_a_directory_above_is_kept(tmp_path: Path) -> None:
    """Found on owner-api: dropping any segment named an unrelated top-level module."""
    db = _ingested_below(
        tmp_path, ["domains.service.credit_wallet", "service.credit_wallet"], "backend", "app"
    )
    with SQLiteStore(str(db)) as store:
        res = resolve_fqn(store, "app.nope.service.credit_wallet")

    assert res.resolved is None


def test_one_segment_may_remain(tmp_path: Path) -> None:
    """What remains starts at the graph's top level: `app.domains` is `domains`."""
    db = _ingested_below(tmp_path, ["domains", "domains.service.fn"], "backend", "app")
    with SQLiteStore(str(db)) as store:
        assert resolve_fqn(store, "app.domains").resolved == "domains"


def test_a_backslash_source_root_is_dropped(tmp_path: Path) -> None:
    db = _ingested_below(tmp_path, ["cgis.run"], "repo", source="packages\\api\\src")
    with SQLiteStore(str(db)) as store:
        assert resolve_fqn(store, "src.cgis.run").resolved == "cgis.run"


def test_nothing_is_dropped_without_a_recorded_root(tmp_path: Path) -> None:
    db = _seed_store(tmp_path / "fqn.db", ["domains.service.credit_wallet"])
    with SQLiteStore(str(db)) as store:
        assert resolve_fqn(store, "app.domains.service.credit_wallet").resolved is None


def test_a_root_recorded_on_windows_is_split_on_backslashes(tmp_path: Path) -> None:
    db = _seed_store(tmp_path / "fqn.db", ["domains.fn"])
    with SQLiteStore(str(db)) as store:
        store.record_ingest("C:\\work\\backend\\app")
        assert resolve_fqn(store, "backend.app.domains.fn").resolved == "domains.fn"
