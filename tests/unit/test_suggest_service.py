"""Unit tests for cgis.query.analysis.suggest_service — package-cohesion orchestration (#242)."""

from pathlib import Path

import pytest
from conftest import make_file_node, make_import_edge

from cgis.core.models import Edge, Node
from cgis.query.analysis.suggest_service import suggest_packages
from cgis.storage.sqlite_store import SQLiteStore


def _store_with(tmp_path: Path, nodes: list[Node], edges: list[Edge]) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = str(tmp_path / "g.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def _two_clusters() -> tuple[list[Node], list[Edge]]:
    files = [make_file_node(f"p.{n}") for n in ("a", "b", "c", "x", "y", "z")]
    edges = [
        make_import_edge(f"p.{s}", f"p.{t}")
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


def test_suggest_none_or_blank_prefix_is_no_signal(tmp_path: Path) -> None:
    """A None/blank prefix (a CLI/MCP client may send either) → no_signal, not a crash."""
    db = _store_with(tmp_path, *_two_clusters())
    for bad in (None, "", "   "):
        report = suggest_packages(db, bad)
        assert report.verdict == "no_signal"
        assert report.note == "no fqn_prefix given"


def test_suggest_no_files_is_no_signal(tmp_path: Path) -> None:
    db = _store_with(tmp_path, [make_file_node("other.a")], [])
    report = suggest_packages(db, prefix="p")
    assert report.verdict == "no_signal"
    assert report.note is not None
    assert "matched 0" in report.note


def test_suggest_mis_rooted_emits_diagnostic(tmp_path: Path) -> None:
    nodes = [make_file_node("p.a"), make_file_node("p.b")]
    edges = [make_import_edge("p.a", "wholly.external.thing")]
    db = _store_with(tmp_path, nodes, edges)
    report = suggest_packages(db, prefix="p")
    assert report.verdict == "no_signal"
    assert report.note is not None
    assert "mis-rooted" in report.note.lower()
    # Files WERE found — the no_signal report must reflect that, not a misleading 0.
    assert report.file_count == 2


def test_suggest_single_module_prefix_is_not_mis_rooted(tmp_path: Path) -> None:
    """A prefix that matches exactly ONE module is 'nothing to split', NOT a false
    'mis-rooted' alarm — even though that lone module imports siblings (found while
    dogfooding cgis.query.drift through the MCP tool)."""
    nodes = [make_file_node("p.solo"), make_file_node("other.x")]
    edges = [make_import_edge("p.solo", "other.x")]  # outbound import to a non-member
    db = _store_with(tmp_path, nodes, edges)
    report = suggest_packages(db, prefix="p.solo")
    assert report.verdict == "no_signal"
    assert report.file_count == 1
    assert report.note is not None
    assert "single module" in report.note
    assert "mis-rooted" not in report.note.lower()


def test_suggest_sparse_package_is_not_split(tmp_path: Path) -> None:
    """A near-disconnected package — two tiny clusters amid many independent helpers
    — must NOT be 'split'. Modularity Q is high (the clusters hold all the edges) but
    it's a sparse-graph artifact, not real structure (the owner-api/utils case found
    cross-repo: 3 edges over 12 files, Q=0.44, 58% isolated). The connectivity guard
    downgrades it to 'leave'."""
    edges = [
        make_import_edge("p.a", "p.b"),
        make_import_edge("p.b", "p.c"),
        make_import_edge("p.a", "p.c"),  # cluster 1: a-b-c triangle
        make_import_edge("p.x", "p.y"),  # cluster 2: x-y
    ]
    files = [make_file_node(f"p.{n}") for n in ("a", "b", "c", "x", "y")]
    files += [make_file_node(f"p.h{i}") for i in range(7)]  # 7 isolated helpers
    db = _store_with(tmp_path, files, edges)
    report = suggest_packages(db, prefix="p")
    assert report.modularity_q >= 0.35  # Q alone would say 'split'...
    assert report.connected_fraction < 0.5  # ...but only 5/12 files are coupled
    assert report.verdict == "leave"  # so the guard downgrades it
    assert report.note is not None
    assert "sparse-graph artifact" in report.note


def test_suggest_dense_split_survives_connectivity_guard(tmp_path: Path) -> None:
    """The guard must NOT over-suppress: a genuinely dense, well-clustered package
    (all files coupled) still reads 'split'."""
    db = _store_with(tmp_path, *_two_clusters())  # two 3-cliques, every file coupled
    report = suggest_packages(db, prefix="p")
    assert report.connected_fraction == pytest.approx(1.0)
    assert report.verdict == "split"


def _two_clusters_nested() -> tuple[list[Node], list[Edge]]:
    """Two cliques whose directory layout (core/ and io/) MATCHES the communities."""
    groups = {"core": ("a", "b", "c"), "io": ("x", "y", "z")}
    files = [make_file_node(f"p.{sub}.{n}") for sub, names in groups.items() for n in names]
    edges = [
        make_import_edge(f"p.{sub}.{s}", f"p.{sub}.{t}")
        for sub, names in groups.items()
        for s in names
        for t in names
        if s != t
    ]
    return files, edges


def test_suggest_aligned_nested_is_aligned(tmp_path: Path) -> None:
    """Nested layout that matches the communities → low divergence → 'aligned'.

    This is the genuine D-boundary case (Q high, D ~ 0): the directories already
    express the dependency communities, so the tool must NOT recommend a split.
    It exercises the divergence threshold that the cgis.guardian self-parsing
    case does not (guardian lands 'borderline' on low Q, not on low D).
    """
    db = _store_with(tmp_path, *_two_clusters_nested())
    report = suggest_packages(db, prefix="p")
    assert report.divergence == pytest.approx(0.0)
    assert report.direction == "matched"
    assert report.verdict == "aligned"


def test_two_ingest_roots_yield_same_verdict(tmp_path: Path) -> None:
    """src/-style (cgis.p.*) and src/cgis/-style (p.*) ingests agree (#242 🔴)."""
    # Root A: files cgis.p.*, targets cgis.p.* (resolve internally).
    files_a = [make_file_node(f"cgis.p.{n}") for n in ("a", "b", "c", "x", "y", "z")]
    edges_a = [
        make_import_edge(f"cgis.p.{s}", f"cgis.p.{t}")
        for grp in (("a", "b", "c"), ("x", "y", "z"))
        for s in grp
        for t in grp
        if s != t
    ]
    db_a = _store_with(tmp_path / "a", files_a, edges_a)

    # Root B: files p.*, but targets written cgis.p.* (the mis-rooted shape).
    files_b = [make_file_node(f"p.{n}") for n in ("a", "b", "c", "x", "y", "z")]
    edges_b = [
        make_import_edge(f"p.{s}", f"cgis.p.{t}")
        for grp in (("a", "b", "c"), ("x", "y", "z"))
        for s in grp
        for t in grp
        if s != t
    ]
    db_b = _store_with(tmp_path / "b", files_b, edges_b)

    ra = suggest_packages(db_a, prefix="cgis.p")
    rb = suggest_packages(db_b, prefix="p")
    assert ra.verdict == rb.verdict == "split"
    assert ra.modularity_q == pytest.approx(rb.modularity_q)
    assert ra.file_count == rb.file_count == 6
