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


def test_a_touched_directory_alone_is_not_stale(tmp_path: Path) -> None:
    """A directory whose mtime moved but which gained nothing is not a change (#175).

    Any write into a directory bumps its mtime — this database when it lives in
    the tree it describes, an editor swap file, a freshly created `__pycache__`.
    Treating that as staleness made the default `cgis ingest . -o graph.db`
    permanently stale, so a suspicious directory is opened and asked whether it
    actually holds something new.
    """
    db, repo = _graph_of(tmp_path, "a.py")
    future = time.time() + 5
    os.utime(repo, (future, future))

    with SQLiteStore(db) as store:
        assert store.freshness().state is FreshnessState.FRESH


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


def test_the_database_living_in_the_tree_does_not_make_it_stale(tmp_path: Path) -> None:
    """`cgis ingest . -o graph.db` is the default, and it must not self-report stale.

    Writing the database bumps the mtime of the directory holding it, and that
    write finishes *after* the ingest is recorded — so a directory-only check
    reported STALE for every graph stored inside the tree it describes. Measured
    before the fix: dir mtime 583.3405 against ingested_at 583.3325.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    db = str(repo / "graph.db")
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
        store.record_ingest(str(repo))
    # Simulate the post-record write that closing the store performs.
    future = time.time() + 5
    os.utime(repo, (future, future))

    with SQLiteStore(db) as store:
        assert store.freshness().state is FreshnessState.FRESH


def test_an_untracked_source_file_still_makes_it_stale(tmp_path: Path) -> None:
    """Ignoring the database must not also ignore a genuinely new source file."""
    db, repo = _graph_of(tmp_path, "a.py")
    (repo / "brand_new.py").write_text("z = 3\n", encoding="utf-8")
    future = time.time() + 5
    os.utime(repo, (future, future))
    os.utime(repo / "brand_new.py", (future, future))

    with SQLiteStore(db) as store:
        result = store.freshness()

    assert result.state is FreshnessState.STALE
    assert result.changed == 1


def test_a_whole_new_subpackage_is_stale(tmp_path: Path) -> None:
    """A new directory of source files must not read as FRESH (#443 review).

    The scan skipped every non-file entry, so an added sub-package was invisible
    — silent under-reporting, the one direction this signal must never fail in.
    """
    db, repo = _graph_of(tmp_path, "a.py")
    (repo / "newpkg").mkdir()
    (repo / "newpkg" / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")
    # mtimes advanced explicitly rather than raced: a change inside one filesystem
    # tick of the ingest lands on the same quantised value (~4 ms here).
    future = time.time() + 5
    os.utime(repo, (future, future))
    os.utime(repo / "newpkg", (future, future))

    with SQLiteStore(db) as store:
        result = store.freshness()

    assert result.state is FreshnessState.STALE
    assert result.changed == 1


def test_an_excluded_directory_appearing_is_not_a_change(tmp_path: Path) -> None:
    """`__pycache__` and friends are not source, and the ingest never read them."""
    db, repo = _graph_of(tmp_path, "a.py")
    (repo / "__pycache__").mkdir()
    (repo / ".venv").mkdir()
    future = time.time() + 5
    os.utime(repo, (future, future))

    with SQLiteStore(db) as store:
        assert store.freshness().state is FreshnessState.FRESH


def test_a_file_dated_in_the_future_does_not_blind_the_probe(tmp_path: Path) -> None:
    """One future mtime must not suppress every later edit (#443 review).

    An unclamped max over file mtimes let a single file — from a tar extraction
    preserving timestamps, clock skew, or a generator calling `os.utime` —
    push `ingested_at` hours ahead and mark real changes FRESH until then.
    """
    db, repo = _graph_of(tmp_path, "a.py", "b.py")
    tomorrow = time.time() + 86400
    os.utime(repo / "a.py", (tomorrow, tomorrow))
    with SQLiteStore(db) as store:
        store.record_ingest(str(repo))
        _root, ingested_at = store.get_ingest_state() or ("", 0.0)
        assert ingested_at <= time.time() + 1, "ingested_at must not be in the future"

        (repo / "b.py").write_text("genuinely edited\n", encoding="utf-8")
        later = time.time() + 5
        os.utime(repo / "b.py", (later, later))
        assert store.freshness().state is FreshnessState.STALE


def test_a_symlinked_root_still_recognises_its_own_database(tmp_path: Path) -> None:
    """The db-exclusion compared unresolved paths, so a symlink reintroduced the bug.

    `cgis ingest link/ -o link/graph.db` where `link -> real/`: scanning yields
    resolved paths while the exclusion set held the unresolved spelling, so the
    database counted as a new source file and the graph was stale forever.
    """
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.py").write_text("x = 1\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real)

    db = str(link / "graph.db")
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
        store.record_ingest(str(link))
    future = time.time() + 5
    os.utime(real, (future, future))

    with SQLiteStore(db) as store:
        assert store.freshness().state is FreshnessState.FRESH


def test_a_deleted_directory_is_counted_once_per_file(tmp_path: Path) -> None:
    """ "N missing" means files, so a gone directory must not add itself to the count."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    nodes = []
    for name in ("pkg/a.py", "pkg/b.py"):
        (repo / name).write_text("x = 1\n", encoding="utf-8")
        nodes.append(
            Node(
                id=name.replace("/", "."),
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

    shutil.rmtree(repo / "pkg")

    with SQLiteStore(db) as store:
        result = store.freshness()

    assert result.missing == 2
