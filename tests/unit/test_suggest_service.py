"""Unit tests for cgis.query.suggest_service — package-cohesion orchestration (#242)."""

from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.suggest_service import suggest_packages
from cgis.storage.sqlite_store import SQLiteStore


def _file(fqn: str) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=fqn.rsplit(".", 1)[-1],
        file_path=fqn.replace(".", "/") + ".py",
        start_line=0,
        end_line=0,
    )


def _imp(src: str, tgt: str) -> Edge:
    return Edge(
        id=f"{src}:IMPORTS:{tgt}",
        source=src,
        target=tgt,
        type=EdgeType.IMPORTS,
        weight=1.0,
        confidence=1.0,
    )


def _store_with(tmp_path: Path, nodes: list[Node], edges: list[Edge]) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = str(tmp_path / "g.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def _two_clusters() -> tuple[list[Node], list[Edge]]:
    files = [_file(f"p.{n}") for n in ("a", "b", "c", "x", "y", "z")]
    edges = [
        _imp(f"p.{s}", f"p.{t}")
        for grp in (("a", "b", "c"), ("x", "y", "z"))
        for s in grp
        for t in grp
        if s != t
    ]
    return files, edges


def test_suggest_flat_two_clusters_is_split(tmp_path: Path) -> None:
    db = _store_with(tmp_path, *_two_clusters())
    report = suggest_packages(db, prefix="p", with_calls=False)
    assert report.verdict == "split"
    assert report.direction == "under_split"
    assert report.divergence == pytest.approx(1.0)
    assert len(report.communities) == 2
    assert report.file_count == 6


def test_suggest_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        suggest_packages(str(tmp_path / "nope.db"), prefix="p")


def test_suggest_no_files_is_no_signal(tmp_path: Path) -> None:
    db = _store_with(tmp_path, [_file("other.a")], [])
    report = suggest_packages(db, prefix="p")
    assert report.verdict == "no_signal"
    assert report.note is not None
    assert "matched 0" in report.note


def test_suggest_mis_rooted_emits_diagnostic(tmp_path: Path) -> None:
    nodes = [_file("p.a"), _file("p.b")]
    edges = [_imp("p.a", "wholly.external.thing")]
    db = _store_with(tmp_path, nodes, edges)
    report = suggest_packages(db, prefix="p")
    assert report.verdict == "no_signal"
    assert report.note is not None
    assert "mis-rooted" in report.note.lower()
