"""Unit tests for the MCP server tools."""

import json
from pathlib import Path

import pytest

from cgis.api.mcp_server import (
    cgis_analyze_impact,
    cgis_drift,
    cgis_get_structure,
    cgis_ingest,
    cgis_trace_flow,
    cgis_validate,
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


def test_cgis_trace_flow_db_error_returns_error(tmp_path: Path) -> None:
    db = tmp_path / "bad.db"
    db.write_bytes(b"not sqlite")

    result = cgis_trace_flow("any.fqn", str(db))

    assert "❌" in result


def test_cgis_analyze_impact_db_error_returns_error(tmp_path: Path) -> None:
    db = tmp_path / "bad.db"
    db.write_bytes(b"not sqlite")

    result = cgis_analyze_impact("any.fqn", str(db))

    assert "❌" in result


def test_cgis_get_structure_db_error_returns_error(tmp_path: Path) -> None:
    db = tmp_path / "bad.db"
    db.write_bytes(b"not sqlite")

    result = cgis_get_structure("any.fqn", str(db))

    assert "❌" in result


def test_cgis_get_structure_unknown_fqn_returns_error(repo_with_calls: tuple[Path, Path]) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_get_structure("nonexistent.fqn", str(db))

    assert "❌" in result


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


# --- cgis_drift tests (#145) ---

_PATTERNS_YAML = """\
version: "1.0.0"
drift_weights:
  cycle_ratio: 1.0
patterns:
  pure_utility:
    description: "x"
    cycle_ratio: {max: 0.0}
project_domains:
  - name: "modroot"
    fqn_prefix: "mod"
    expected_pattern: pure_utility
    drift_tolerance: 0.15
"""


def test_cgis_drift_returns_json(repo_with_calls: tuple[Path, Path]) -> None:
    """cgis_drift returns valid JSON with any_critical, domains, and quotient keys."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))
    patterns = repo / "patterns.yaml"
    patterns.write_text(_PATTERNS_YAML, encoding="utf-8")

    result = cgis_drift(str(db), str(patterns))

    payload = json.loads(result)
    assert payload["any_critical"] is False
    assert payload["domains"][0]["fqn_prefix"] == "mod"
    assert payload["quotient"] == []
    assert payload["max_drift"] == 0.50


def test_cgis_drift_missing_db_returns_error(tmp_path: Path) -> None:
    """cgis_drift returns an error string when the db file does not exist."""
    result = cgis_drift(str(tmp_path / "no.db"), str(tmp_path / "p.yaml"))
    assert "❌" in result


def test_cgis_drift_missing_patterns_returns_error(
    repo_with_calls: tuple[Path, Path],
) -> None:
    """cgis_drift returns an error string when the patterns file does not exist."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))
    result = cgis_drift(str(db), str(repo / "missing.yaml"))
    assert "❌" in result


# --- cgis_validate tests (#145) ---


def test_cgis_validate_returns_json(repo_with_calls: tuple[Path, Path]) -> None:
    """cgis_validate returns valid JSON with edge stats and health verdict."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_validate(str(db))

    payload = json.loads(result)
    assert payload["total"] > 0
    assert payload["threshold"] == 0.30
    assert isinstance(payload["healthy"], bool)
    assert isinstance(payload["top_unresolved"], list)
    for name, _count in payload["top_unresolved"]:
        assert not name.startswith("raw_call:")


def test_cgis_validate_missing_db_returns_error(tmp_path: Path) -> None:
    """cgis_validate returns an error string when the db file does not exist."""
    result = cgis_validate(str(tmp_path / "no.db"))
    assert "❌" in result


# --- suffix FQN resolution in nav tools (#145) ---


def test_cgis_analyze_impact_resolves_suffix(repo_with_calls: tuple[Path, Path]) -> None:
    """A bare suffix FQN resolves to the full FQN and adds a resolved note."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_analyze_impact("callee", str(db), depth=3)  # bare name

    assert "```mermaid" in result
    assert "Resolved 'callee'" in result
    assert "mod.callee" in result


def test_cgis_trace_flow_ambiguous_lists_candidates(tmp_path: Path) -> None:
    """An ambiguous bare FQN returns an error listing all candidates."""
    (tmp_path / "one.py").write_text("def fn(): pass\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"
    cgis_ingest(str(tmp_path), str(db))

    result = cgis_trace_flow("fn", str(db))

    assert "❌ Ambiguous FQN 'fn'" in result
    assert "one.fn" in result
    assert "two.fn" in result


def test_cgis_get_structure_exact_fqn_has_no_note(
    repo_with_calls: tuple[Path, Path],
) -> None:
    """An exact FQN match produces no 'Resolved' note in the output."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_get_structure("mod.caller", str(db))

    assert "```mermaid" in result
    assert "Resolved" not in result
