"""`cgis_analyze_impact` sized for an agent (#551).

On owner-api the A/B agent's impact query came back at 51k characters, and 119
of its 140 nodes were tests or files reached through the import closure. These
pin the three cuts: test callers dropped and counted, files as dead ends of a
symbol's walk, and a node budget that cuts the nearest level it cannot fit.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from cgis.api.mcp_server import cgis_analyze_impact, cgis_ingest
from cgis.query.engine import QueryEngine, impact_screen
from cgis.storage.sqlite_store import SQLiteStore

_SOURCES = {
    "pkg/core.py": "def target():\n    pass\n",
    "pkg/svc.py": (
        "from pkg.core import target\n\ndef use():\n    target()\n\ndef other():\n    target()\n"
    ),
    "pkg/api.py": "from pkg.svc import use\n\ndef route():\n    use()\n",
    # Imports the file that imports `use`: one step further up the import closure.
    "pkg/main.py": "import pkg.api\n",
    "tests/test_core.py": "from pkg.core import target\n\ndef test_target():\n    target()\n",
}


@pytest.fixture
def db(tmp_path: Path) -> str:
    """A five-file repo: a chain of callers, a file importing a file, and a test."""
    for rel, source in _SOURCES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    db_path = str(tmp_path / "graph.db")
    cgis_ingest(str(tmp_path), db_path)
    return db_path


def _impact(db: str, fqn: str, include_tests: bool = False, max_nodes: int = 100) -> dict[str, Any]:
    """The JSON impact payload for `fqn`, at a depth that reaches every caller."""
    payload: dict[str, Any] = json.loads(
        cgis_analyze_impact(
            fqn,
            db,
            depth=5,
            output_format="json",
            include_tests=include_tests,
            max_nodes=max_nodes,
        )
    )
    return payload


def _fqns(payload: dict[str, Any]) -> set[str]:
    """The node ids of a payload."""
    return {node["fqn"] for node in payload["nodes"]}


def test_test_callers_are_left_out_and_counted(db: str) -> None:
    payload = _impact(db, "pkg.core.target")

    assert not any(fqn.startswith("tests.") for fqn in _fqns(payload))
    assert not any(edge["src"].startswith("tests.") for edge in payload["edges"]), (
        "an edge into a dropped node would name a node the payload does not have"
    )
    # The test function and the test file that imports `target`.
    assert payload["excluded_tests"] == 2


def test_include_tests_returns_them(db: str) -> None:
    payload = _impact(db, "pkg.core.target", include_tests=True)

    assert "tests.test_core.test_target" in _fqns(payload)
    assert "excluded_tests" not in payload


def test_production_callers_are_all_still_there(db: str) -> None:
    """The cut is tests and the import closure, not the call chain."""
    assert {"pkg.svc.use", "pkg.svc.other", "pkg.api.route"} <= _fqns(
        _impact(db, "pkg.core.target")
    )


def test_a_file_importing_the_symbol_is_a_dead_end(db: str) -> None:
    """`pkg.api` imports `use`, so it is listed; `pkg.main` only imports `pkg.api`."""
    fqns = _fqns(_impact(db, "pkg.core.target"))

    assert "pkg.api" in fqns
    assert "pkg.main" not in fqns


def test_a_files_own_impact_still_follows_its_importers(db: str) -> None:
    assert "pkg.main" in _fqns(_impact(db, "pkg.api"))


def test_the_budget_cuts_the_level_that_does_not_fit(db: str) -> None:
    """Root plus three callers at depth 1 against a budget of 2: one fits, in id order."""
    payload = _impact(db, "pkg.core.target", max_nodes=2)

    assert _fqns(payload) == {"pkg.core.target", "pkg.svc"}
    assert payload["truncated"] == {"depth": 1, "omitted": 2, "max_nodes": 2}
    assert {(edge["src"], edge["dst"]) for edge in payload["edges"]} == {
        ("pkg.svc", "pkg.core.target")
    }


def test_the_budget_keeps_whole_levels_that_fit(db: str) -> None:
    """Depth 1 is root + three callers; `pkg.api.route` and `pkg.api` are depth 2."""
    payload = _impact(db, "pkg.core.target", max_nodes=5)

    assert {"pkg.svc", "pkg.svc.use", "pkg.svc.other"} <= _fqns(payload)
    assert payload["truncated"]["depth"] == 2
    assert payload["truncated"]["omitted"] == 1


def test_an_answer_within_budget_says_nothing_about_truncation(db: str) -> None:
    assert "truncated" not in _impact(db, "pkg.core.target")


def test_the_diagram_names_the_cut_and_the_way_round_it(db: str) -> None:
    text = cgis_analyze_impact("pkg.core.target", db, depth=5, max_nodes=2)

    assert "2 test caller(s) left out" in text
    assert "Truncated at depth 1" in text
    # Lowering the depth cannot help a cut at depth 1.
    assert "lower depth" not in text


def test_the_diagram_suggests_a_lower_depth_past_the_first_level(db: str) -> None:
    text = cgis_analyze_impact("pkg.core.target", db, depth=5, max_nodes=5)

    assert "Truncated at depth 2" in text
    assert "lower depth" in text


def test_the_engine_without_a_screen_or_budget_is_unchanged(db: str) -> None:
    """The screen is opt-in: context, audit and the guardian walk as before."""
    with SQLiteStore(db) as store:
        result = QueryEngine(store).get_impact_result("pkg.core.target", max_depth=5)

    ids = {node.id for node in result.nodes}
    assert {"tests.test_core.test_target", "pkg.main"} <= ids
    assert result.excluded == 0
    assert result.truncation is None


def test_the_screen_drops_tests_and_marks_files_as_leaves(db: str) -> None:
    with SQLiteStore(db) as store:
        verdict = impact_screen(store, "pkg.core.target", include_tests=False)(
            ["tests.test_core.test_target", "pkg.svc", "pkg.svc.use"]
        )

    assert verdict.dropped == {"tests.test_core.test_target"}
    assert verdict.leaves == {"pkg.svc"}
