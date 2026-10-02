"""Opt-in refresh of a stale graph before an MCP read tool answers (#175)."""

import os
import sqlite3
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cgis.api.auto_refresh import AUTO_REFRESH_ENV, refresh_if_stale
from cgis.api.mcp_server import cgis_find_symbol, cgis_ingest
from cgis.cli import app
from cgis.core.freshness import FreshnessState
from cgis.storage.sqlite_store import SQLiteStore

_PAST = time.time() - 100


def _repo(tmp_path: Path, rel: str = "mod.py") -> Path:
    """A one-module repo whose mtimes all sit in the past.

    Edits are then dated explicitly between that past and now, so the test does
    not depend on how the filesystem quantises timestamps, and a refresh can
    leave the graph FRESH (a future-dated edit would stay newer than the mark).
    """
    repo = tmp_path / "repo"
    path = repo / rel
    path.parent.mkdir(parents=True)
    path.write_text("def old_fn() -> int:\n    return 1\n", encoding="utf-8")
    for p in (path, *path.parents):
        os.utime(p, (_PAST, _PAST))
        if p == repo:
            break
    return repo


def _add_function(path: Path) -> None:
    """Append a function — a signature change, so the refresh is a full rebuild."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\ndef new_fn() -> int:\n    return 2\n")
    os.utime(path, (_PAST + 50, _PAST + 50))


def _ids(db: Path) -> set[str]:
    with SQLiteStore(str(db)) as store:
        return {n.id for n in store.get_all_nodes()}


def _state(db: Path) -> FreshnessState:
    with SQLiteStore(str(db)) as store:
        return store.freshness().state


def test_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the opt-in a read tool never writes: the graph stays stale."""
    monkeypatch.delenv(AUTO_REFRESH_ENV, raising=False)
    repo = _repo(tmp_path)
    db = tmp_path / "graph.db"
    cgis_ingest(str(repo), str(db))
    _add_function(repo / "mod.py")

    assert refresh_if_stale(str(db)) is False
    assert "mod.new_fn" not in _ids(db)
    assert _state(db) is FreshnessState.STALE


def test_a_read_tool_answers_from_the_refreshed_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opted in, a symbol added after the ingest is found by the next query."""
    monkeypatch.setenv(AUTO_REFRESH_ENV, "1")
    repo = _repo(tmp_path)
    db = tmp_path / "graph.db"
    cgis_ingest(str(repo), str(db))
    _add_function(repo / "mod.py")

    result = cgis_find_symbol("new_fn", db_path=str(db))

    assert "mod.new_fn" in result
    assert _state(db) is FreshnessState.FRESH


def test_a_fresh_graph_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second of two callers finds the first's refresh done and does nothing."""
    monkeypatch.setenv(AUTO_REFRESH_ENV, "1")
    repo = _repo(tmp_path)
    db = tmp_path / "graph.db"
    cgis_ingest(str(repo), str(db))
    _add_function(repo / "mod.py")

    assert refresh_if_stale(str(db)) is True
    assert refresh_if_stale(str(db)) is False


def test_a_missing_database_is_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no database there is no recorded root to ingest from."""
    monkeypatch.setenv(AUTO_REFRESH_ENV, "1")
    db = tmp_path / "graph.db"

    assert refresh_if_stale(str(db)) is False
    assert not db.exists()


def test_a_graph_without_recorded_options_is_not_refreshed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A graph that may have been built with --source-root is never re-ingested by guess."""
    monkeypatch.setenv(AUTO_REFRESH_ENV, "1")
    repo = _repo(tmp_path)
    db = tmp_path / "graph.db"
    cgis_ingest(str(repo), str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM ingest_state WHERE key = 'ingest_options'")
    _add_function(repo / "mod.py")

    assert refresh_if_stale(str(db)) is False
    assert _state(db) is FreshnessState.STALE


def test_a_refresh_keeps_the_source_roots_it_was_ingested_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cgis ingest --source-root src` names nodes `pkg.mod`; a refresh must too.

    Re-ingesting with the default options would rename every node `src.pkg.mod`,
    leaving a different graph rather than a fresher one.
    """
    monkeypatch.setenv(AUTO_REFRESH_ENV, "1")
    repo = _repo(tmp_path, "src/pkg/mod.py")
    db = tmp_path / "graph.db"
    result = CliRunner().invoke(
        app, ["ingest", str(repo), "-o", str(db), "-i", "--source-root", "src"]
    )
    assert result.exit_code == 0, result.output
    assert "pkg.mod.old_fn" in _ids(db)
    _add_function(repo / "src/pkg/mod.py")

    assert refresh_if_stale(str(db)) is True

    ids = _ids(db)
    assert "pkg.mod.new_fn" in ids
    assert not any(i.startswith("src.") for i in ids)
