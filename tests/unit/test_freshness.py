"""Unit tests for the graph-freshness value objects (#175)."""

import os
import shutil
import time
from pathlib import Path
from typing import IO, Any

import pytest

from cgis.core.freshness import Freshness, FreshnessState
from cgis.core.models import Node, NodeType
from cgis.storage.sqlite_store import SQLiteStore


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


def _graph_of(tmp_path: Path, *names: str) -> tuple[str, Path]:
    """A repo of trivial modules and a graph built from it, both on disk."""
    repo = tmp_path / "repo"
    repo.mkdir()
    nodes = []
    for name in names:
        (repo / name).write_text("x = 1\n", encoding="utf-8")
        nodes.append(
            Node(
                id=name.removesuffix(".py"),
                type=NodeType.FILE,
                name=name,
                file_path=name,
                start_line=1,
                end_line=1,
            )
        )
    db = str(tmp_path / "g.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, [])
        store.record_ingest(str(repo))
    return db, repo


def test_an_untouched_tree_is_fresh(tmp_path: Path) -> None:
    """Nothing newer, nothing gone."""
    db, _repo = _graph_of(tmp_path, "a.py", "b.py")
    with SQLiteStore(db) as store:
        assert store.freshness().state is FreshnessState.FRESH


def test_a_touched_file_is_stale(tmp_path: Path) -> None:
    """An edit after the ingest is what the reader needs to know about."""
    db, repo = _graph_of(tmp_path, "a.py", "b.py")
    future = time.time() + 5
    os.utime(repo / "a.py", (future, future))
    with SQLiteStore(db) as store:
        result = store.freshness()
    assert result.state is FreshnessState.STALE
    assert result.changed == 1
    assert result.missing == 0


def test_a_deleted_file_is_stale(tmp_path: Path) -> None:
    """A file the graph still describes but the tree no longer has."""
    db, repo = _graph_of(tmp_path, "a.py", "b.py")
    (repo / "b.py").unlink()
    with SQLiteStore(db) as store:
        result = store.freshness()
    assert result.state is FreshnessState.STALE
    assert result.missing == 1
    # `changed` is deliberately not asserted. A deletion does bump the directory's
    # mtime, but within one filesystem tick of the ingest that bump lands on the
    # same quantised value and is invisible — measured at ~4 ms on this
    # filesystem. The file-level signal is what makes this case reliable.


def test_a_new_file_is_stale_through_its_directory(tmp_path: Path) -> None:
    """The probe never walks, so an added file is seen via its directory (#175).

    The directory's mtime is advanced explicitly rather than by racing the clock.
    That a real addition bumps it is a filesystem property, measured separately —
    a directory's mtime moves on add, on delete and on a new subdirectory, and
    does not move when a file inside it is edited. What this test pins is the
    probe's half: that a directory newer than the ingest makes the graph stale,
    with no file having changed.

    Writing the file and checking immediately is flaky by construction — mtimes
    are quantised (~4 ms here) so a change within one tick of the ingest lands on
    the same value. That blind spot is real and documented; it is not what this
    test is for.
    """
    db, repo = _graph_of(tmp_path, "a.py")
    (repo / "brand_new.py").write_text("z = 3\n", encoding="utf-8")
    future = time.time() + 5
    os.utime(repo, (future, future))
    with SQLiteStore(db) as store:
        result = store.freshness()
    assert result.state is FreshnessState.STALE
    assert result.changed == 1
    assert result.missing == 0


def test_a_graph_without_ingest_state_is_unknown(tmp_path: Path) -> None:
    """Never FRESH: an unanswerable question must not read as a clean bill."""
    db = str(tmp_path / "old.db")
    with SQLiteStore(db) as store:
        store.save_graph(
            [
                Node(
                    id="a",
                    type=NodeType.FILE,
                    name="a.py",
                    file_path="a.py",
                    start_line=1,
                    end_line=1,
                )
            ],
            [],
        )
        result = store.freshness()
    assert result.state is FreshnessState.UNKNOWN
    assert "predates" in (result.reason or "")


def test_a_moved_root_is_unknown_not_stale(tmp_path: Path) -> None:
    """A graph queried from elsewhere cannot be checked; say so, do not guess."""
    db, repo = _graph_of(tmp_path, "a.py")
    shutil.rmtree(repo)
    with SQLiteStore(db) as store:
        result = store.freshness()
    assert result.state is FreshnessState.UNKNOWN
    assert "root" in (result.reason or "")


def test_an_explicit_root_overrides_the_recorded_one(tmp_path: Path) -> None:
    """The moved-checkout escape hatch: point the probe at where the tree is now."""
    db, repo = _graph_of(tmp_path, "a.py")
    moved = tmp_path / "moved"
    repo.rename(moved)
    with SQLiteStore(db) as store:
        assert store.freshness(root=str(moved)).state is FreshnessState.FRESH


def test_the_probe_never_reads_file_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec D5, asserted rather than reviewed.

    Reaching for content hashes would move the per-query cost from milliseconds
    to seconds without failing anything else, so the ban is a test.
    """
    db, _repo = _graph_of(tmp_path, "a.py", "b.py")
    opened: list[str] = []
    real_open = Path.open

    def spy(self: Path, *args: object, **kwargs: object) -> IO[Any]:
        opened.append(str(self))
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", spy)
    with SQLiteStore(db) as store:
        store.freshness()
    assert [p for p in opened if p.endswith(".py")] == []
