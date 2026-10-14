"""`cgis_find_symbol`'s `fqn_prefix` and the directories above the graph (#584).

In the 2026-10-14 flow A/B two of three forced sessions asked for
`fqn_prefix="src.cgis"` on a graph ingested from `src/`, whose ids start at
`cgis.`, and got nothing. #568 taught `resolve_fqn` to drop such a directory;
the prefix filter is a separate entry point and was left out.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cgis.api.mcp_server import (
    cgis_audit_reachability,
    cgis_find_orphans,
    cgis_find_symbol,
    cgis_ingest,
    cgis_metrics,
    cgis_suggest_packages,
)
from cgis.cli import app


@pytest.fixture
def db(tmp_path: Path) -> str:
    """A graph ingested from `src/`: ids start with `pkg.`."""
    source = tmp_path / "src" / "pkg"
    source.mkdir(parents=True)
    (source / "mod.py").write_text("def ingest():\n    pass\n", encoding="utf-8")
    db_path = str(tmp_path / "graph.db")
    cgis_ingest(str(tmp_path / "src"), db_path)
    return db_path


def _fqns(db: str, prefix: str) -> list[str]:
    return [m["fqn"] for m in json.loads(cgis_find_symbol("ingest", db, fqn_prefix=prefix))]


def test_a_prefix_built_from_the_path_finds_what_the_graph_calls_it(db: str) -> None:
    assert _fqns(db, "src.pkg") == _fqns(db, "pkg") == ["pkg.mod.ingest"]


def test_a_prefix_that_matches_nothing_after_the_drop_still_finds_nothing(db: str) -> None:
    assert _fqns(db, "src.nope") == []


def test_the_directory_alone_does_not_widen_the_search_to_everything(db: str) -> None:
    """Dropping the whole prefix would answer a scoped question with the entire graph."""
    assert _fqns(db, "src") == []


def test_only_a_directory_above_the_graph_is_dropped(db: str) -> None:
    """`other` is not where the graph was ingested from; `other.pkg` scopes nothing."""
    assert _fqns(db, "other.pkg") == []


def _ingest(root: Path, files: dict[str, str]) -> str:
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    db_path = str(root.parent / f"{root.name}.db")
    cgis_ingest(str(root), db_path)
    return db_path


def test_a_real_package_named_like_the_root_keeps_its_scope(tmp_path: Path) -> None:
    """`proj/proj`-style layout: `app.x` is real code, not `x` with a directory in front.

    Retried whenever the query found nothing, `app.x` answered with the sibling
    tree's `x.m.target` (#585 review).
    """
    db = _ingest(
        tmp_path / "app",
        {"app/x/m.py": "def other():\n    pass\n", "x/m.py": "def target():\n    pass\n"},
    )

    assert json.loads(cgis_find_symbol("target", db, fqn_prefix="app.x")) == []


def test_phantom_nodes_from_absolute_imports_do_not_hold_the_scope(tmp_path: Path) -> None:
    """On owner-api, `from app.pkg.mod import ingest` mints an EXTERNAL `app.pkg.mod.ingest`.

    The first search matched that phantom, so a retry-on-empty never fired and the
    real `pkg.mod.ingest` stayed hidden (#585 review).
    """
    db = _ingest(
        tmp_path / "app",
        {
            "pkg/mod.py": "def ingest():\n    pass\n",
            "main.py": "from app.pkg.mod import ingest\n\ndef run():\n    ingest()\n",
        },
    )

    assert "pkg.mod.ingest" in [
        m["fqn"] for m in json.loads(cgis_find_symbol("ingest", db, fqn_prefix="app.pkg"))
    ]


def test_every_prefix_entry_point_reads_a_directory_above_the_graph(tmp_path: Path) -> None:
    """Orphans, metrics' scope, suggest-packages and audit: the same rule, one helper."""
    db = _ingest(
        tmp_path / "src",
        {
            "pkg/a.py": "class Lonely:\n    pass\n\ndef entry():\n    helper()\n",
            "pkg/b.py": "from pkg.a import entry\n\ndef helper():\n    pass\n",
        },
    )

    orphans = json.loads(cgis_find_orphans(db, prefix="src.pkg"))
    assert orphans["considered"] > 0
    metrics = json.loads(cgis_metrics(db, scope=["src.pkg"]))
    assert metrics["resolution"]["total_edges"] > 0
    suggest = json.loads(cgis_suggest_packages(db, prefix="src.pkg"))
    assert suggest["file_count"] == 2
    audit = cgis_audit_reachability("pkg.b.helper", db, from_prefix="src.pkg")
    assert "No sources matched" not in audit


def test_the_cli_find_reads_it_too(tmp_path: Path) -> None:
    db = _ingest(tmp_path / "src", {"pkg/mod.py": "def ingest():\n    pass\n"})

    result = CliRunner().invoke(app, ["find", "ingest", "--db", db, "--prefix", "src.pkg"])

    assert "pkg.mod.ingest" in result.stdout
