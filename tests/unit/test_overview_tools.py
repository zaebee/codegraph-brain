"""`cgis overview` and `cgis_overview` — the two front doors to the same map (#478)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cgis.api.mcp_server import (
    cgis_find_symbol,
    cgis_get_structure,
    cgis_ingest,
    cgis_overview,
)
from cgis.cli import app

runner = CliRunner()

# Both package shapes on purpose: `app.domains` has an __init__.py and so becomes a
# node, `app.core` has none and becomes no node at all. The flat fixture this file
# started with could only produce module prefixes — the one shape that always
# resolves — so the acceptance test below was green on a promise that is false on
# any real tree (#486 review).
_REPO = {
    "app/__init__.py": "",
    "app/api.py": "def get_user():\n    return 1\n\n\ndef post_user():\n    return 2\n",
    "app/store.py": "def save():\n    return 3\n",
    "app/domains/__init__.py": "",
    "app/domains/admin.py": "def ban():\n    return 4\n",
    "app/core/settings.py": "def load():\n    return 5\n",
    "tests/__init__.py": "",
    "tests/test_api.py": "def test_get_user():\n    assert True\n",
}


@pytest.fixture
def repo_db(tmp_path: Path) -> str:
    for rel, text in _REPO.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    db = str(tmp_path / "graph.db")
    assert "✅" in cgis_ingest(str(tmp_path), db)
    return db


def _report(db: str) -> dict:
    result = cgis_overview(db)
    start = result.find("{")
    assert start != -1, f"expected JSON, got: {result}"
    return json.loads(result[start:])


def test_overview_tool_reports_sizes_and_packages(repo_db: str) -> None:
    """The first call in an unfamiliar repo answers with something, not a prompt for a name."""
    report = _report(repo_db)

    assert report["files"] == 8
    assert report["symbols"]["FUNCTION"] == 6
    assert {row["prefix"] for row in report["packages"]} == {
        "app.api",
        "app.store",
        "app.domains",
        "app.core",
    }
    assert [row["prefix"] for row in report["test_packages"]] == ["tests.test_api"]
    # A fresh graph adds no freshness key — the shape only grows when there is
    # something to say (#175).
    assert "freshness" not in report


def test_every_prefix_the_overview_prints_finds_symbols(repo_db: str) -> None:
    """The map is only useful if its rows are the next call's argument (#478 acceptance).

    The next call is `cgis_find_symbol(fqn_prefix=…)`, which matches on the prefix
    string. `cgis_get_structure` is not — see the test below.
    """
    report = _report(repo_db)
    prefixes = [row["prefix"] for row in report["packages"] + report["test_packages"]]
    assert {"app.domains", "app.core"} <= set(prefixes)

    for prefix in prefixes:
        answer = cgis_find_symbol("a", repo_db, fqn_prefix=prefix)
        assert not answer.startswith("❌"), f"{prefix}: {answer}"
        hits = json.loads(answer[answer.find("[") :])
        assert hits, f"{prefix} scopes a search to nothing"
        assert all(hit["fqn"].startswith(prefix) for hit in hits)


def test_a_package_prefix_is_not_something_structure_can_look_up(repo_db: str) -> None:
    """Why the docs send an agent through find_symbol first (#486 review).

    `app.core` has no `__init__.py`, so nothing in the graph bears that name;
    `app.domains` has one, and its node holds no members because containment runs
    file → symbol. Pinned so a graph model that adds package nodes fails here.
    """
    missing = cgis_get_structure("app.core", repo_db, output_format="json")
    assert missing.startswith("❌")

    empty = cgis_get_structure("app.domains", repo_db, output_format="json")
    payload = json.loads(empty[empty.find("{") :])
    assert [node["fqn"] for node in payload["nodes"]] == ["app.domains"]
    assert payload["edges"] == []


def test_overview_tool_reports_a_missing_database(tmp_path: Path) -> None:
    """A friendly ❌ naming the fix, like every other tool."""
    assert "Database not found" in cgis_overview(str(tmp_path / "absent.db"))


def test_overview_output_stays_small(repo_db: str) -> None:
    """The map exists to save context; a ceiling is what makes that true."""
    assert len(cgis_overview(repo_db)) < 2000


def test_cli_overview_prints_a_table(repo_db: str) -> None:
    """Text is the default, as for every other command."""
    result = runner.invoke(app, ["overview", "--db", repo_db], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    assert "app.api" in result.stdout
    assert "Test packages" in result.stdout


def test_cli_overview_json_matches_the_tool(repo_db: str) -> None:
    """Same numbers whichever entry point asked, as with trace/impact (#481)."""
    result = runner.invoke(app, ["overview", "--db", repo_db, "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    report = _report(repo_db)
    assert payload["packages"] == report["packages"]
    assert payload["symbols"] == report["symbols"]


def test_cli_overview_rejects_mermaid(repo_db: str) -> None:
    """There is no diagram of a package census; say so instead of rendering nonsense."""
    result = runner.invoke(app, ["overview", "--db", repo_db, "--format", "mermaid"])

    assert result.exit_code == 2
    assert "text or json" in result.output


def test_cli_overview_missing_database_exits_one(tmp_path: Path) -> None:
    """Consistent with the other commands: 1 for a missing graph."""
    result = runner.invoke(app, ["overview", "--db", str(tmp_path / "absent.db")])

    assert result.exit_code == 1
    assert "Database not found" in result.output
