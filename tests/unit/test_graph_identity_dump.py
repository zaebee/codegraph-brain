"""The canonical graph dump the identity net compares (spec: Migration, safety net)."""

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import graph_identity as gi  # noqa: E402

from cgis.extractors.registry import build_extractors  # noqa: E402
from cgis.pipeline import IngestionPipeline  # noqa: E402
from cgis.storage.sqlite_store import SQLiteStore  # noqa: E402


def _ingest(root: Path, db: Path) -> None:
    """As `cgis ingest` does it, ingest state included."""
    with SQLiteStore(str(db)) as store:
        pipeline = IngestionPipeline(build_extractors([]))
        pipeline.run(str(root), store=store)
        store.record_ingest(str(root), pipeline.observed_mtimes)
        store.record_ingest_options([], None)


def _repo(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def f():\n    g()\n\ndef g():\n    pass\n")
    return root


def test_dump_carries_nodes_edges_and_ingest_state(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    _ingest(root, tmp_path / "g.db")
    out = gi.dump(tmp_path / "g.db", root)

    assert {n["id"] for n in out["nodes"]} >= {"pkg.a", "pkg.a.f", "pkg.a.g"}
    call = next(e for e in out["edges"] if e["type"] == "CALLS")
    assert (call["source"], call["target"]) == ("pkg.a.f", "pkg.a.g")
    assert "ingested_at" not in out["ingest_state"]
    assert set(out["nodes"][0]) >= {"metadata", "language", "is_test", "is_generated"}


def test_paths_are_normalised(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    _ingest(root, tmp_path / "g.db")
    text = json.dumps(gi.dump(tmp_path / "g.db", root))

    assert str(root) not in text
    assert "<ROOT>" in text


def test_the_same_ingest_twice_dumps_identically(tmp_path: Path) -> None:
    a, b = _repo(tmp_path / "a"), _repo(tmp_path / "b")
    _ingest(a, tmp_path / "a.db")
    _ingest(b, tmp_path / "b.db")

    assert gi.diff(gi.dump(tmp_path / "a.db", a), gi.dump(tmp_path / "b.db", b)) == []


def test_diff_names_a_changed_edge(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    _ingest(root, tmp_path / "g.db")
    before = gi.dump(tmp_path / "g.db", root)
    with sqlite3.connect(tmp_path / "g.db") as conn:
        conn.execute("UPDATE edges SET confidence = 0.5 WHERE type = 'CALLS'")
    after = gi.dump(tmp_path / "g.db", root)

    lines = gi.diff(before, after)
    assert lines, "a changed confidence must show"
    assert all(line.startswith("edges ") for line in lines), "only edge rows changed"
    assert any("pkg.a.f" in line and "0.5" in line for line in lines)


def test_dump_carries_what_uplift_writes(tmp_path: Path) -> None:
    """Uplift sets ontology_class and domains; step 5 moves it (step-0 review)."""
    root = _repo(tmp_path / "r")
    _ingest(root, tmp_path / "g.db")
    node = gi.dump(tmp_path / "g.db", root)["nodes"][0]

    assert {"ontology_class", "domains", "confidence_score"} <= set(node)


def test_diff_names_a_changed_ingest_state(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    _ingest(root, tmp_path / "g.db")
    before = gi.dump(tmp_path / "g.db", root)
    with sqlite3.connect(tmp_path / "g.db") as conn:
        conn.execute("UPDATE ingest_state SET value = '[\"x\"]' WHERE key = 'npm_dependencies'")
    after = gi.dump(tmp_path / "g.db", root)

    assert gi.diff(before, after) == [
        f"ingest_state: {before['ingest_state']} → {after['ingest_state']}"
    ]
