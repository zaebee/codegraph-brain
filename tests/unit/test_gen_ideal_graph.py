"""Tests for scripts/gen_ideal_graph.py."""

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Import directly so tests don't depend on subprocess
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from gen_ideal_graph import PATTERNS, generate, generate_from_ontology


@pytest.mark.parametrize("pattern", [*PATTERNS, "all"])
def test_generate_returns_valid_schema(pattern: str) -> None:
    graph = generate(pattern)
    assert set(graph.keys()) == {"metadata", "nodes", "edges"}
    assert graph["metadata"]["node_count"] == len(graph["nodes"])
    assert graph["metadata"]["edge_count"] == len(graph["edges"])


@pytest.mark.parametrize("pattern", [*PATTERNS, "all"])
def test_no_duplicate_node_ids(pattern: str) -> None:
    graph = generate(pattern)
    ids = [n["id"] for n in graph["nodes"]]
    assert len(ids) == len(set(ids)), f"Duplicate node IDs in pattern '{pattern}'"


@pytest.mark.parametrize("pattern", [*PATTERNS, "all"])
def test_no_dangling_edges(pattern: str) -> None:
    graph = generate(pattern)
    node_ids = {n["id"] for n in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in node_ids, f"Missing source {edge['source']}"
        assert edge["target"] in node_ids, f"Missing target {edge['target']}"


@pytest.mark.parametrize("pattern", [*PATTERNS, "all"])
def test_all_nodes_have_health_metadata(pattern: str) -> None:
    graph = generate(pattern)
    for node in graph["nodes"]:
        meta = node["metadata"]
        assert "fan_in" in meta
        assert "fan_out" in meta
        assert "depth" in meta
        assert "in_cycle" in meta


@pytest.mark.parametrize("pattern", [*PATTERNS, "all"])
def test_all_nodes_are_internal(pattern: str) -> None:
    graph = generate(pattern)
    for node in graph["nodes"]:
        assert node["namespace"] == "INTERNAL"
        assert math.isclose(node["confidence_score"], 1.0)


def test_hub_has_high_fan_in_utils() -> None:
    graph = generate("hub")
    funcs = {n["id"]: n for n in graph["nodes"] if n["type"] == "FUNCTION"}
    hub_funcs = [n for fqn, n in funcs.items() if fqn.startswith("utils.")]
    assert all(n["metadata"]["fan_in"] > 1 for n in hub_funcs)
    assert all(n["metadata"]["fan_out"] == 0 for n in hub_funcs)


def test_chain_is_linear() -> None:
    graph = generate("chain")
    calls = [e for e in graph["edges"] if e["type"] == "CALLS"]
    # linear: each stage calls at most one other
    sources = [e["source"] for e in calls]
    assert len(sources) == len(set(sources)), "Chain should have at most one CALLS per source"


def test_star_orchestrator_calls_all_leaves() -> None:
    graph = generate("star")
    calls = [e for e in graph["edges"] if e["type"] == "CALLS"]
    orchestrator_calls = [e for e in calls if "orchestrator" in e["source"]]
    leaf_targets = {e["target"] for e in orchestrator_calls}
    assert len(leaf_targets) >= 4


def test_dag_has_no_cycles() -> None:
    graph = generate("dag")
    calls = [e for e in graph["edges"] if e["type"] in ("CALLS", "IMPORTS")]
    adj: dict[str, list[str]] = {}
    for e in calls:
        adj.setdefault(e["source"], []).append(e["target"])

    # DFS cycle detection: 0=unvisited, 1=in-progress, 2=done
    color = {n["id"]: 0 for n in graph["nodes"]}

    def has_cycle(node: str) -> bool:
        color[node] = 1
        for nb in adj.get(node, []):
            if color.get(nb) == 1:
                return True
            if color.get(nb) == 0 and has_cycle(nb):
                return True
        color[node] = 2
        return False

    for node_id, state in color.items():
        if state == 0:
            assert not has_cycle(node_id), f"Cycle detected in dag pattern at {node_id}"


@pytest.mark.parametrize("pattern", [*PATTERNS, "all"])
def test_edge_ids_match_source_target(pattern: str) -> None:
    graph = generate(pattern)
    for edge in graph["edges"]:
        expected = f"{edge['source']}:{edge['type'].lower()}:{edge['target']}"
        assert edge["id"] == expected, f"Edge ID mismatch: {edge['id']!r} != {expected!r}"


def test_unknown_pattern_raises() -> None:
    with pytest.raises(ValueError, match="Unknown pattern"):
        generate("nonexistent")


def test_cli_stdout() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/gen_ideal_graph.py", "--pattern", "hub"],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).parent.parent.parent,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["metadata"]["node_count"] > 0


def test_cli_output_file(tmp_path: Path) -> None:
    out = tmp_path / "ideal.json"
    result = subprocess.run(
        [sys.executable, "scripts/gen_ideal_graph.py", "--pattern", "all", "--output", str(out)],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).parent.parent.parent,
    )
    assert result.returncode == 0
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["metadata"]["node_count"] == 41


_MINIMAL_PATTERNS = """\
version: "1.0.0"
drift_weights:
  hub_count: 0.15
  star_count: 0.15
  chain_len: 0.10
  dag_depth: 0.10
  router_count: 0.10
  cycle_ratio: 0.25
  unresolved_ratio: 0.15
patterns:
  pure_utility:
    description: "Hub"
    hub_count: {min: 1}
    cycle_ratio: {max: 0.0}
  layered_dag:
    description: "DAG"
    dag_depth: {min: 3}
    cycle_ratio: {max: 0.0}
project_domains:
  - name: extraction
    fqn_prefix: cgis.extractors
    expected_pattern: pure_utility
    drift_tolerance: 0.15
  - name: query
    fqn_prefix: cgis.query
    expected_pattern: layered_dag
    drift_tolerance: 0.20
"""


def test_from_ontology_returns_valid_schema() -> None:
    """generate_from_ontology returns a graph with metadata, nodes, edges."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_MINIMAL_PATTERNS)
        patterns_path = f.name

    graph = generate_from_ontology(patterns_path)
    assert set(graph.keys()) == {"metadata", "nodes", "edges"}
    assert graph["metadata"]["node_count"] == len(graph["nodes"])
    assert graph["metadata"]["edge_count"] == len(graph["edges"])


def test_from_ontology_no_duplicate_node_ids() -> None:
    """Nodes generated for multiple domains must have unique IDs."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_MINIMAL_PATTERNS)
        patterns_path = f.name

    graph = generate_from_ontology(patterns_path)
    ids = [n["id"] for n in graph["nodes"]]
    assert len(ids) == len(set(ids)), "Duplicate node IDs"


def test_from_ontology_node_ids_use_domain_prefix() -> None:
    """All node IDs must start with the domain fqn_prefix."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_MINIMAL_PATTERNS)
        patterns_path = f.name

    graph = generate_from_ontology(patterns_path)
    known_prefixes = {"cgis.extractors", "cgis.query"}
    for node in graph["nodes"]:
        nid = node["id"]
        assert any(nid == p or nid.startswith(p + ".") for p in known_prefixes), (
            f"Node ID '{nid}' does not match any domain prefix"
        )


def test_from_ontology_no_dangling_edges() -> None:
    """All edge source/target must refer to existing node IDs."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_MINIMAL_PATTERNS)
        patterns_path = f.name

    graph = generate_from_ontology(patterns_path)
    node_ids = {n["id"] for n in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids
