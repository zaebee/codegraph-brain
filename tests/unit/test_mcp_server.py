"""Unit tests for the MCP server tools."""

import json
from pathlib import Path

import pytest
import yaml

from cgis.api.mcp_server import (
    cgis_analyze_impact,
    cgis_drift,
    cgis_find_symbol,
    cgis_get_structure,
    cgis_ingest,
    cgis_init_ontology,
    cgis_trace_flow,
    cgis_validate,
)
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.storage.sqlite_store import SQLiteStore


@pytest.fixture
def repo_with_calls(tmp_path: Path) -> tuple[Path, Path]:
    """Two-file repo: caller() calls callee() (resolves); extra.py calls ghost_fn() (unresolved)."""
    (tmp_path / "mod.py").write_text(
        "def caller():\n    callee()\n\ndef callee(): pass\n", encoding="utf-8"
    )
    (tmp_path / "extra.py").write_text("def x():\n    ghost_fn()\n", encoding="utf-8")
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


def test_cgis_trace_flow_json_returns_joinable_payload(
    repo_with_calls: tuple[Path, Path],
) -> None:
    """`output_format='json'` returns parseable {root, nodes, edges} with real FQNs (#171)."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_trace_flow("mod.caller", str(db), depth=3, output_format="json")

    assert "```mermaid" not in result
    payload = json.loads(result)
    assert payload["root"].endswith("mod.caller")
    assert any(n["fqn"].endswith("mod.callee") for n in payload["nodes"])
    assert all({"src", "dst", "type", "confidence"} <= e.keys() for e in payload["edges"])


def test_cgis_analyze_impact_json_returns_joinable_payload(
    repo_with_calls: tuple[Path, Path],
) -> None:
    """impact JSON exposes upstream callers as edge sources for set ops (#171)."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_analyze_impact("mod.callee", str(db), depth=3, output_format="json")

    payload = json.loads(result)
    assert payload["root"].endswith("mod.callee")
    assert any(e["src"].endswith("mod.caller") for e in payload["edges"])


def test_cgis_get_structure_json_returns_joinable_payload(
    repo_with_calls: tuple[Path, Path],
) -> None:
    """structure JSON is parseable and rooted at the resolved FQN (#171)."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_get_structure("mod.caller", str(db), depth=2, output_format="json")

    payload = json.loads(result)
    assert payload["root"].endswith("mod.caller")


def test_cgis_trace_flow_unknown_format_returns_error(
    repo_with_calls: tuple[Path, Path],
) -> None:
    """An unknown output_format is an explicit error, not a silent mermaid fallback (#171)."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_trace_flow("mod.caller", str(db), output_format="xml")

    assert "❌" in result
    assert "xml" in result


def test_cgis_get_structure_uses_structural_not_call_edges(
    repo_with_calls: tuple[Path, Path],
) -> None:
    """structure traverses CONTAINS/DECLARES only — a CALLS-reached callee must not appear.

    Regression for the #209 review HIGH: cgis_get_structure previously called
    get_flow_graph (call-graph) instead of get_structural_graph (containment).
    """
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_get_structure("mod.caller", str(db), output_format="json")

    payload = json.loads(result)
    # caller() calls callee(), but a *structural* view of a function has no callee
    assert all(not n["fqn"].endswith("mod.callee") for n in payload["nodes"])
    assert all(e["type"] != "CALLS" for e in payload["edges"])


def test_trace_flow_blank_fqn_short_circuits(tmp_path: Path) -> None:
    """A blank FQN is rejected before any store access (mirrors #173 search guard)."""
    # db deliberately does not exist — the blank guard must fire first
    result = cgis_trace_flow("   ", str(tmp_path / "graph.db"))

    assert "❌" in result
    assert "empty" in result.lower()


def test_analyze_impact_blank_fqn_short_circuits(tmp_path: Path) -> None:
    """analyze_impact also short-circuits on blank input before the store."""
    result = cgis_analyze_impact("", str(tmp_path / "graph.db"))

    assert "❌" in result
    assert "empty" in result.lower()


def test_get_structure_blank_fqn_short_circuits(tmp_path: Path) -> None:
    """get_structure also short-circuits on blank input before the store."""
    result = cgis_get_structure("\t", str(tmp_path / "graph.db"))

    assert "❌" in result
    assert "empty" in result.lower()


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
    assert payload["max_drift"] == pytest.approx(0.50)


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
    """cgis_validate returns valid JSON with edge stats and health verdict.

    The repo_with_calls fixture includes extra.py which calls ghost_fn() — an
    undefined name that cannot be resolved.  top_unresolved must therefore be
    non-empty and each entry must have the raw_call: prefix stripped.
    """
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_validate(str(db))

    payload = json.loads(result)
    assert payload["total"] > 0
    assert payload["threshold"] == pytest.approx(0.30)
    assert isinstance(payload["healthy"], bool)
    assert isinstance(payload["top_unresolved"], list)
    # ghost_fn() cannot be resolved — at least one unresolved entry expected
    assert len(payload["top_unresolved"]) > 0
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


# --- cgis_drift profile + empty status tests (#178) ---

_PATTERNS_YAML_EMPTY = """\
version: "1.0.0"
drift_weights:
  hub_count: 1.0
  star_count: 0.0
  chain_len: 0.0
  dag_depth: 0.0
  router_count: 0.0
  cycle_ratio: 0.0
  unresolved_ratio: 0.0
patterns:
  needs_hub:
    description: "x"
    hub_count: {min: 10}
project_domains:
  - name: "misfire"
    fqn_prefix: "totally.missing"
    expected_pattern: needs_hub
    drift_tolerance: 0.10
"""

_PATTERNS_YAML_PROFILE = """\
version: "1.0.0"
drift_weights:
  hub_count: 1.0
  star_count: 0.0
  chain_len: 0.0
  dag_depth: 0.0
  router_count: 0.0
  cycle_ratio: 0.0
  unresolved_ratio: 0.0
patterns:
  needs_hub:
    description: "x"
    hub_count: {min: 10}
project_domains:
  - name: "pydom"
    fqn_prefix: "totally.missing"
    expected_pattern: needs_hub
    drift_tolerance: 0.10
    profile: python
"""


def test_cgis_drift_mistargeted_prefix_reports_empty_and_any_critical(
    repo_with_calls: tuple[Path, Path],
) -> None:
    """A mis-targeted fqn_prefix produces status='empty', non-null note, and any_critical=True."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))
    patterns = repo / "patterns_empty.yaml"
    patterns.write_text(_PATTERNS_YAML_EMPTY, encoding="utf-8")

    result = cgis_drift(str(db), str(patterns))

    payload = json.loads(result)
    assert payload["any_critical"] is True
    domain = payload["domains"][0]
    assert domain["status"] == "empty"
    assert domain["note"] is not None
    assert "matched 0 nodes" in domain["note"]


def test_cgis_drift_profile_filters_python_domain(
    repo_with_calls: tuple[Path, Path],
) -> None:
    """profile='typescript' filters out the python-profile domain; payload has no rows."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))
    patterns = repo / "patterns_profile.yaml"
    patterns.write_text(_PATTERNS_YAML_PROFILE, encoding="utf-8")

    result = cgis_drift(str(db), str(patterns), profile="typescript")

    payload = json.loads(result)
    assert payload["any_critical"] is False
    assert payload["domains"] == []


def test_cgis_find_symbol_returns_ranked_json(tmp_path: Path) -> None:
    """cgis_find_symbol resolves a partial name to ranked candidate FQNs (#173)."""
    (tmp_path / "mod.py").write_text(
        "def get_user(): pass\ndef get_user_by_id(): pass\n", encoding="utf-8"
    )
    db = tmp_path / "graph.db"
    cgis_ingest(str(tmp_path), str(db))

    data = json.loads(cgis_find_symbol("get_user", str(db)))
    assert {d["name"] for d in data} >= {"get_user", "get_user_by_id"}
    assert set(data[0].keys()) == {"fqn", "name", "type", "file", "line"}
    # exact match ranks before the prefix match
    exact_idx = next(i for i, d in enumerate(data) if d["name"] == "get_user")
    prefix_idx = next(i for i, d in enumerate(data) if d["name"] == "get_user_by_id")
    assert exact_idx < prefix_idx


def test_cgis_find_symbol_kind_filter(tmp_path: Path) -> None:
    """The kind filter restricts results by node type (#173)."""
    (tmp_path / "mod.py").write_text(
        "class UserThing: pass\ndef user_fn(): pass\n", encoding="utf-8"
    )
    db = tmp_path / "graph.db"
    cgis_ingest(str(tmp_path), str(db))

    data = json.loads(cgis_find_symbol("ser", str(db), kind="class"))
    names = {d["name"] for d in data}
    assert "UserThing" in names
    assert "user_fn" not in names


def test_cgis_find_symbol_missing_db(tmp_path: Path) -> None:
    assert "❌" in cgis_find_symbol("x", str(tmp_path / "nope.db"))


def test_cgis_find_symbol_blank_kind_is_no_filter(tmp_path: Path) -> None:
    """A whitespace-only kind means 'no type filter', not 'match nothing' (#173)."""
    (tmp_path / "mod.py").write_text("def get_user(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"
    cgis_ingest(str(tmp_path), str(db))
    data = json.loads(cgis_find_symbol("get_user", str(db), kind="   "))
    assert any(d["name"] == "get_user" for d in data)


# ---------------------------------------------------------------------------
# cgis_init_ontology tests (#174 task 4)
# ---------------------------------------------------------------------------


def _make_chain_db(tmp_path: Path) -> str:
    """Build a 12-node CALLS chain database for cgis_init_ontology tests."""
    db = str(tmp_path / "chain.db")
    nodes = [
        Node(
            id=f"app.chain.f{i}",
            type=NodeType.FUNCTION,
            name=f"f{i}",
            file_path=f"app/chain/f{i}.py",
            start_line=1,
            end_line=2,
        )
        for i in range(12)
    ]
    edges = [
        Edge(
            id=f"e{i}",
            source=f"app.chain.f{i}",
            target=f"app.chain.f{i + 1}",
            type=EdgeType.CALLS,
        )
        for i in range(11)
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def test_cgis_init_ontology_returns_yaml_text(tmp_path: Path) -> None:
    """Returns parseable yaml with project_domains; writes NO files."""
    db = _make_chain_db(tmp_path)
    before = set(tmp_path.iterdir())
    result = cgis_init_ontology(db_path=db)
    assert "project_domains:" in result
    assert yaml.safe_load(result)["project_domains"]
    assert set(tmp_path.iterdir()) == before  # read-only surface


def test_cgis_init_ontology_missing_db_message(tmp_path: Path) -> None:
    """Missing db → the ❌ message string, no exception, no db created."""
    missing = tmp_path / "none.db"
    result = cgis_init_ontology(db_path=str(missing))
    assert result.startswith("❌ Database not found")
    assert not missing.exists()
