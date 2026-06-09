"""Tests for scripts/gen_ideal_graph.py."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Import directly so tests don't depend on subprocess
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from gen_ideal_graph import PATTERNS, generate


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
        assert node["confidence_score"] == 1.0


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

    for node_id in list(color):
        if color[node_id] == 0:
            assert not has_cycle(node_id), f"Cycle detected in dag pattern at {node_id}"


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
