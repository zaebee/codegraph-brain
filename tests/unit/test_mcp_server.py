"""Unit tests for the MCP server tools."""

import json
import os
import re
from pathlib import Path

import pytest
import yaml
from conftest import fit_patterns_yaml, make_chain_db

from cgis.api.mcp_server import (
    cgis_analyze_impact,
    cgis_audit_reachability,
    cgis_context,
    cgis_drift,
    cgis_find_symbol,
    cgis_get_structure,
    cgis_ingest,
    cgis_init_ontology,
    cgis_metrics,
    cgis_suggest_packages,
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
    assert "Graph total:" in result
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


def test_cgis_drift_payload_includes_tangle_ratio(
    repo_with_calls: tuple[Path, Path],
) -> None:
    """Each domain in the cgis_drift payload carries a tangle_ratio field (#186)."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))
    patterns = repo / "patterns.yaml"
    patterns.write_text(_PATTERNS_YAML, encoding="utf-8")

    payload = json.loads(cgis_drift(str(db), str(patterns)))

    assert all("tangle_ratio" in d for d in payload["domains"])
    assert all(0.0 <= d["tangle_ratio"] <= 1.0 for d in payload["domains"])


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


def test_cgis_init_ontology_returns_yaml_text(tmp_path: Path) -> None:
    """Returns parseable yaml with project_domains; writes NO files."""
    db = make_chain_db(tmp_path)
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


def test_cgis_context_returns_xml_package(tmp_path: Path) -> None:
    """cgis_context compiles an XML package with focal/callers/callees for an agent."""
    db = make_chain_db(tmp_path)
    result = cgis_context("app.chain.f5", str(db))
    assert '<context focal="app.chain.f5"' in result
    assert "app.chain.f4" in result  # upstream caller
    assert "app.chain.f6" in result  # downstream callee


def test_cgis_context_blank_fqn_guarded(tmp_path: Path) -> None:
    """A blank FQN is rejected before touching the store (mirrors the nav tools)."""
    db = make_chain_db(tmp_path)
    assert cgis_context("   ", str(db)).startswith("❌ FQN cannot be empty")


def test_cgis_context_missing_db_returns_error(tmp_path: Path) -> None:
    """A missing database returns a friendly ❌ message, not an exception."""
    missing = tmp_path / "missing.db"
    result = cgis_context("x.y", str(missing))
    assert result.startswith("❌ Database not found")
    assert not missing.exists()


def _graph_total_nodes(summary: str) -> int:
    """Parse the 'Graph total: N nodes' count out of a cgis_ingest summary."""
    m = re.search(r"Graph total: (\d+) nodes", summary)
    assert m, f"no graph total in: {summary}"
    return int(m.group(1))


def test_cgis_ingest_noop_reingest_reports_stable_total(tmp_path: Path) -> None:
    """A no-change re-ingest reports the whole-graph total, not a shrunken working set (#192)."""
    (tmp_path / "a.py").write_text("def fa():\n    fb()\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def fb(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"

    first = cgis_ingest(str(tmp_path), str(db))
    again = cgis_ingest(str(tmp_path), str(db))  # nothing changed

    total = _graph_total_nodes(first)
    assert total > 0
    assert _graph_total_nodes(again) == total  # no apparent data loss


def test_cgis_ingest_full_rebuild_drops_stale_nodes(tmp_path: Path) -> None:
    """full_rebuild=True re-scans from scratch, so a removed file's nodes disappear (#192)."""
    (tmp_path / "a.py").write_text("def fa(): pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def fb(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"

    both = _graph_total_nodes(cgis_ingest(str(tmp_path), str(db)))
    (tmp_path / "b.py").unlink()
    rebuilt = cgis_ingest(str(tmp_path), str(db), full_rebuild=True)

    assert "full rebuild" in rebuilt
    assert _graph_total_nodes(rebuilt) < both  # fb's node is gone


def test_cgis_ingest_incremental_reports_changed_and_total(tmp_path: Path) -> None:
    """Incremental mode surfaces both the per-run delta and the whole-graph total."""
    (tmp_path / "m.py").write_text("def f(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"

    result = cgis_ingest(str(tmp_path), str(db))

    assert "mode: incremental" in result
    assert "Graph total:" in result


def test_cgis_ingest_noop_reports_no_files_changed(tmp_path: Path) -> None:
    """An incremental no-op says so explicitly, so the stable total reads correctly (#192)."""
    (tmp_path / "m.py").write_text("def f(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"

    cgis_ingest(str(tmp_path), str(db))
    again = cgis_ingest(str(tmp_path), str(db))

    assert "No files changed" in again


def test_cgis_ingest_full_rebuild_repopulates_files_state(tmp_path: Path) -> None:
    """full_rebuild leaves files_state valid so the next incremental is a no-op (#223 review)."""
    (tmp_path / "m.py").write_text("def f(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"

    cgis_ingest(str(tmp_path), str(db), full_rebuild=True)
    nxt = cgis_ingest(str(tmp_path), str(db))  # nothing changed since the rebuild

    # If full_rebuild left files_state stale, this run would re-parse instead of no-op.
    assert "No files changed" in nxt


def test_cgis_metrics_returns_report(tmp_path: Path) -> None:
    """cgis_metrics returns a JSON architecture report (#16)."""
    db = make_chain_db(tmp_path)
    payload = json.loads(cgis_metrics(str(db)))
    assert "bottlenecks" in payload
    assert "god_classes" in payload


def test_cgis_metrics_missing_db_returns_error(tmp_path: Path) -> None:
    """A missing database returns a friendly ❌ message, not an exception."""
    result = cgis_metrics(str(tmp_path / "missing.db"))
    assert result.startswith("❌ Database not found")


def test_cgis_drift_payload_has_coverage_and_fit(tmp_path: Path) -> None:
    """cgis_drift JSON carries top-level coverage and per-report fit (#177)."""
    db = str(tmp_path / "g.db")
    nodes = [
        Node(
            id=f"dom.f{i}",
            type=NodeType.FUNCTION,
            name=f"f{i}",
            file_path="dom.py",
            start_line=i,
            end_line=i + 1,
        )
        for i in (1, 2, 3)
    ] + [
        Node(
            id="orphan.y",
            type=NodeType.FUNCTION,
            name="y",
            file_path="orphan.py",
            start_line=1,
            end_line=2,
        )
    ]
    edges = [
        Edge(id="e1", source="dom.f1", target="dom.f3", type=EdgeType.CALLS),
        Edge(id="e2", source="dom.f2", target="dom.f3", type=EdgeType.CALLS),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    p = tmp_path / "p.yaml"
    p.write_text(fit_patterns_yaml())
    payload = json.loads(cgis_drift(db_path=db, patterns_path=str(p), max_drift=1.0))
    assert any(c.startswith("orphan") for c in payload["coverage"])
    assert payload["domains"][0]["fit"]["nearest_template"] == "pure_utility"
    assert payload["domains"][0]["fit"]["band"] == "good"


def _audit_graph_db(tmp_path: Path) -> str:
    nodes = [
        Node(
            id="app.h1",
            type=NodeType.ROUTE_HANDLER,
            name="h1",
            file_path="r.py",
            start_line=1,
            end_line=2,
        ),
        Node(
            id="app.h2",
            type=NodeType.ROUTE_HANDLER,
            name="h2",
            file_path="r.py",
            start_line=4,
            end_line=5,
        ),
        Node(
            id="app.guard",
            type=NodeType.FUNCTION,
            name="guard",
            file_path="a.py",
            start_line=1,
            end_line=2,
        ),
    ]
    edges = [Edge(id="e1", source="app.h1", target="app.guard", type=EdgeType.CALLS)]
    db = str(tmp_path / "audit.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def test_cgis_audit_reachability_reports_gaps(tmp_path: Path) -> None:
    """cgis_audit_reachability returns covered/gaps JSON; h2 never reaches the guard (#172)."""
    db = _audit_graph_db(tmp_path)
    payload = json.loads(cgis_audit_reachability("app.guard", db, from_type="ROUTE_HANDLER"))
    assert payload["target"] == "app.guard"
    assert {g["fqn"] for g in payload["gaps"]} == {"app.h2"}
    assert {c["fqn"] for c in payload["covered"]} == {"app.h1"}


def test_cgis_audit_unknown_node_type_errors(tmp_path: Path) -> None:
    """An invalid from_type is a friendly ❌, not a crash."""
    db = _audit_graph_db(tmp_path)
    assert cgis_audit_reachability("app.guard", db, from_type="NOPE").startswith(
        "❌ Unknown node type"
    )


def test_cgis_audit_requires_selector(tmp_path: Path) -> None:
    """No from_type/from_prefix → clear error."""
    db = _audit_graph_db(tmp_path)
    assert "from_type or from_prefix" in cgis_audit_reachability("app.guard", db)


def test_cgis_audit_whitespace_prefix_is_rejected(tmp_path: Path) -> None:
    """A whitespace-only from_prefix is treated as unset → friendly guard, not a raw ValueError."""
    db = _audit_graph_db(tmp_path)
    result = cgis_audit_reachability("app.guard", db, from_prefix="   ")
    assert "from_type or from_prefix" in result


def test_cgis_audit_none_params_do_not_crash(tmp_path: Path) -> None:
    """JSON null for from_type/from_prefix must not crash on .strip() (#236)."""
    db = _audit_graph_db(tmp_path)
    payload = json.loads(
        cgis_audit_reachability("app.guard", db, from_type=None, from_prefix="app")
    )
    assert payload["target"] == "app.guard"
    assert {g["fqn"] for g in payload["gaps"]} == {"app.h2"}


# --- cgis_suggest_packages tests (#242) ---


def test_cgis_suggest_packages_returns_json(repo_with_calls: tuple[Path, Path]) -> None:
    """cgis_suggest_packages returns a JSON payload with the cohesion verdict (#242)."""
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))
    # repo_with_calls creates mod.py and extra.py; prefix "mod" exists in the graph
    payload = json.loads(cgis_suggest_packages(str(db), prefix="mod"))
    assert "verdict" in payload
    assert "modularity_q" in payload
    assert "direction" in payload
    assert isinstance(payload["communities"], list)


def test_cgis_suggest_packages_missing_db(tmp_path: Path) -> None:
    """A missing db returns an error string, not a crash (#242)."""
    result = cgis_suggest_packages(str(tmp_path / "no.db"), prefix="mod")
    assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# db_path guard for cgis_ingest (#312)
# ---------------------------------------------------------------------------
#
# cgis_ingest is the only MCP tool that creates its database — the other twelve
# refuse a db_path that does not already exist. It therefore needs a different
# guard, not the same one: it reads untrusted repository content and is then
# told by the same agent where to write, so a hostile db_path is a real shape.


def test_ingest_refuses_to_clobber_a_non_sqlite_file(tmp_path: Path) -> None:
    """An existing file that is not a database must survive untouched."""
    victim = tmp_path / "config.db"
    original = "PermitRootLogin no\n"
    victim.write_text(original, encoding="utf-8")
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")

    result = cgis_ingest(str(tmp_path), str(victim))

    assert "❌" in result
    assert victim.read_text(encoding="utf-8") == original, "the target file was modified"


def test_ingest_refuses_to_create_parent_directories(tmp_path: Path) -> None:
    """A missing parent must be an error, not an invitation to build a tree."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    nested = tmp_path / "does" / "not" / "exist" / "graph.db"

    result = cgis_ingest(str(tmp_path), str(nested))

    assert "❌" in result
    assert not nested.parent.exists(), "parent directories were created"


def test_ingest_refuses_an_unexpected_suffix(tmp_path: Path) -> None:
    """Only database-shaped names are accepted, so `~/.ssh/config` cannot be a target."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    target = tmp_path / "authorized_keys"

    result = cgis_ingest(str(tmp_path), str(target))

    assert "❌" in result
    assert not target.exists()


def test_ingest_refuses_a_directory_as_db_path(tmp_path: Path) -> None:
    """A directory target must be rejected rather than surfacing a sqlite error."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    target = tmp_path / "graph.db"
    target.mkdir()

    result = cgis_ingest(str(tmp_path), str(target))

    assert "❌" in result
    assert target.is_dir()


def test_ingest_accepts_an_existing_empty_file(tmp_path: Path) -> None:
    """An empty placeholder is what SQLite itself would create — not a clobber."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    placeholder = tmp_path / "graph.db"
    placeholder.touch()

    result = cgis_ingest(str(tmp_path), str(placeholder))

    assert "✅" in result
    # Not just "it did not refuse": the placeholder must now be a real database,
    # otherwise the guard could pass while ingestion quietly wrote nothing.
    assert placeholder.stat().st_size > 0
    with placeholder.open("rb") as fh:
        assert fh.read(16) == b"SQLite format 3\x00"


def test_ingest_still_accepts_an_existing_database(tmp_path: Path) -> None:
    """The guard must not break re-ingesting into a real database."""
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"
    assert "✅" in cgis_ingest(str(tmp_path), str(db))

    result = cgis_ingest(str(tmp_path), str(db), full_rebuild=True)

    assert "✅" in result


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
def test_ingest_reports_an_unreadable_path_instead_of_raising(tmp_path: Path) -> None:
    """The guard runs before cgis_ingest's own try/except, so it must not raise.

    `Path.is_dir()` and friends propagate OSError (a PermissionError on the
    parent, for one). Without handling, that escapes the MCP tool as a crash
    rather than a message the agent can act on.
    """
    (tmp_path / "mod.py").write_text("def fn(): pass\n", encoding="utf-8")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "graph.db").write_text("junk", encoding="utf-8")
    locked.chmod(0o000)
    try:
        result = cgis_ingest(str(tmp_path), str(locked / "graph.db"))
    finally:
        locked.chmod(0o755)

    assert "❌" in result
