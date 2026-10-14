"""`cgis_find_symbol`'s `fqn_prefix` and the directories above the graph (#584).

In the 2026-10-14 flow A/B two of three forced sessions asked for
`fqn_prefix="src.cgis"` on a graph ingested from `src/`, whose ids start at
`cgis.`, and got nothing. #568 taught `resolve_fqn` to drop such a directory;
the prefix filter is a separate entry point and was left out.
"""

import json
from pathlib import Path

import pytest

from cgis.api.mcp_server import cgis_find_symbol, cgis_ingest


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
