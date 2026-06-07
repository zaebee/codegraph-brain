"""Unit tests for the MCP server tools."""

from pathlib import Path

import pytest

from cgis.api.mcp_server import (
    cgis_analyze_impact,
    cgis_get_structure,
    cgis_ingest,
    cgis_trace_flow,
)
from cgis.storage.sqlite_store import SQLiteStore


@pytest.fixture
def repo_with_calls(tmp_path: Path) -> tuple[Path, Path]:
    """Two-file repo where caller() calls callee()."""
    (tmp_path / "mod.py").write_text(
        "def caller():\n    callee()\n\ndef callee(): pass\n", encoding="utf-8"
    )
    db = tmp_path / "graph.db"
    return tmp_path, db


def test_cgis_ingest_returns_summary(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def func(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"

    result = cgis_ingest(str(tmp_path), str(db))

    assert "✅" in result
    assert "Nodes:" in result
    assert db.exists()


def test_cgis_ingest_nonexistent_path_returns_error(tmp_path: Path) -> None:
    result = cgis_ingest(str(tmp_path / "no_such_dir"), str(tmp_path / "graph.db"))

    assert "❌" in result


def test_cgis_ingest_file_path_returns_error(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text("def fn(): pass\n", encoding="utf-8")

    result = cgis_ingest(str(f), str(tmp_path / "graph.db"))

    assert "❌" in result


def test_cgis_trace_flow_returns_mermaid(repo_with_calls: tuple[Path, Path]) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_trace_flow("mod.caller", str(db), depth=3)

    assert "```mermaid" in result
    assert "graph TD" in result


def test_cgis_trace_flow_unknown_fqn_returns_error(repo_with_calls: tuple[Path, Path]) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_trace_flow("nonexistent.fqn", str(db))

    assert "❌" in result


def test_cgis_analyze_impact_returns_mermaid(repo_with_calls: tuple[Path, Path]) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_analyze_impact("mod.callee", str(db), depth=3)

    assert "```mermaid" in result
    assert "graph TD" in result


def test_cgis_analyze_impact_unknown_fqn_returns_error(
    repo_with_calls: tuple[Path, Path],
) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_analyze_impact("nonexistent.fqn", str(db))

    assert "❌" in result


def test_cgis_get_structure_returns_mermaid(repo_with_calls: tuple[Path, Path]) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_get_structure("mod.caller", str(db), depth=2)

    assert "```mermaid" in result


def test_cgis_trace_flow_missing_db_returns_error(tmp_path: Path) -> None:
    result = cgis_trace_flow("any.fqn", str(tmp_path / "missing.db"))

    assert "❌" in result
    assert "cgis_ingest" in result


def test_cgis_analyze_impact_missing_db_returns_error(tmp_path: Path) -> None:
    result = cgis_analyze_impact("any.fqn", str(tmp_path / "missing.db"))

    assert "❌" in result
    assert "cgis_ingest" in result


def test_cgis_get_structure_missing_db_returns_error(tmp_path: Path) -> None:
    result = cgis_get_structure("any.fqn", str(tmp_path / "missing.db"))

    assert "❌" in result
    assert "cgis_ingest" in result


def test_cgis_ingest_overwrites_on_repeat_call(tmp_path: Path) -> None:
    """Calling ingest twice must not duplicate nodes."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"

    cgis_ingest(str(tmp_path), str(db))
    cgis_ingest(str(tmp_path), str(db))

    with SQLiteStore(str(db)) as store:
        nodes = store.get_nodes_by_file("mod.py")
    # FILE node + FUNCTION node — no duplicates from re-ingest
    assert len(nodes) == 2
