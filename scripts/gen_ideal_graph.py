"""Generate a synthetic "ideal architecture" graph.json for UI demo and testing.

Usage:
    uv run python scripts/gen_ideal_graph.py --pattern hub --output ideal_hub.json
    uv run python scripts/gen_ideal_graph.py --pattern chain
    uv run python scripts/gen_ideal_graph.py --pattern star
    uv run python scripts/gen_ideal_graph.py --pattern dag
    uv run python scripts/gen_ideal_graph.py --pattern all --output ideal.json

Patterns
--------
hub   — One central utility called by many; depends on nothing.
        Demonstrates high fan-in / low fan-out (healthy shared library).
chain — Linear A→B→C→D pipeline.
        Single-responsibility modules, no branching.
star  — One orchestrator calls N leaf services (no leaf-to-leaf edges).
        Watch: if leaves grow too many, risks God Object.
dag   — Multi-level directed acyclic graph with no cycles.
        Represents a clean layered architecture.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# Type alias for a JSON-like graph node/edge dict
GraphDict = dict[str, Any]

# ── helpers ──────────────────────────────────────────────────────────────────


def _node(
    fqn: str,
    name: str,
    node_type: str,
    file_path: str,
    start_line: int,
    end_line: int,
    fan_in: int = 0,
    fan_out: int = 0,
    depth: int = 0,
) -> GraphDict:
    return {
        "id": fqn,
        "type": node_type,
        "name": name,
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "language": "python",
        "ontology_class": None,
        "domains": [],
        "namespace": "INTERNAL",
        "confidence_score": 1.0,
        "metadata": {
            "fan_in": fan_in,
            "fan_out": fan_out,
            "depth": depth,
            "in_cycle": False,
        },
    }


def _file_node(module: str, filename: str, line_count: int = 20) -> GraphDict:
    return _node(module, filename, "FILE", filename, 1, line_count)


def _func_node(
    fqn: str, name: str, file_path: str, line: int, fan_in: int = 0, fan_out: int = 0
) -> GraphDict:
    return _node(
        fqn, name, "FUNCTION", file_path, line, line + 8, fan_in=fan_in, fan_out=fan_out, depth=1
    )


def _edge(
    source: str, target: str, edge_type: str, file_path: str = "", confidence: float = 1.0
) -> GraphDict:
    return {
        "id": f"{source}:{edge_type.lower()}:{target}",
        "source": source,
        "target": target,
        "type": edge_type,
        "weight": 1.0,
        "confidence": confidence,
        "context": None,
        "file_path": file_path,
        "line_number": None,
    }


def _contains(file_fqn: str, func_fqn: str, file_path: str) -> GraphDict:
    return _edge(file_fqn, func_fqn, "CONTAINS", file_path)


def _calls(src: str, tgt: str) -> GraphDict:
    return _edge(src, tgt, "CALLS")


def _imports(src_file: str, tgt_file: str) -> GraphDict:
    return _edge(src_file, tgt_file, "IMPORTS")


# ── patterns ─────────────────────────────────────────────────────────────────


def build_hub() -> tuple[list[GraphDict], list[GraphDict]]:
    """Central utils module called by many consumers; depends on nothing.

    utils ←── service_a
         ←── service_b
         ←── service_c
         ←── service_d
    """
    callers = ["service_a", "service_b", "service_c", "service_d"]
    nodes: list[GraphDict] = []
    edges: list[GraphDict] = []

    # hub: utils
    utils_file = "utils.py"
    fmt_fqn = "utils.format_response"
    val_fqn = "utils.validate_input"
    nodes.append(_file_node("utils", utils_file, 30))
    nodes.append(
        _func_node(fmt_fqn, "format_response", utils_file, 5, fan_in=len(callers), fan_out=0)
    )
    nodes.append(
        _func_node(val_fqn, "validate_input", utils_file, 15, fan_in=len(callers), fan_out=0)
    )
    edges.append(_contains("utils", fmt_fqn, utils_file))
    edges.append(_contains("utils", val_fqn, utils_file))

    # callers
    for svc in callers:
        fp = f"{svc}.py"
        nodes.append(_file_node(svc, fp))
        handler = f"{svc}.handle"
        nodes.append(_func_node(handler, "handle", fp, 5, fan_in=0, fan_out=2))
        edges.append(_contains(svc, handler, fp))
        edges.append(_imports(svc, "utils"))
        edges.append(_calls(handler, fmt_fqn))
        edges.append(_calls(handler, val_fqn))

    return nodes, edges


def build_chain() -> tuple[list[GraphDict], list[GraphDict]]:
    """Linear pipeline: ingest → parse → transform → emit.

    Each stage has exactly one dependency on the next — single-responsibility.
    """
    stages = [
        ("ingest", "ingest.py", "fetch_data", "Reads raw data from source"),
        ("parse", "parse.py", "parse_records", "Parses raw bytes into records"),
        ("transform", "transform.py", "transform_records", "Applies business rules"),
        ("emit", "emit.py", "emit_results", "Writes output to sink"),
    ]
    nodes: list[GraphDict] = []
    edges: list[GraphDict] = []

    for i, (module, fp, fn, _) in enumerate(stages):
        fan_out = 1 if i < len(stages) - 1 else 0
        fan_in = 1 if i > 0 else 0
        nodes.append(_file_node(module, fp))
        nodes.append(_func_node(f"{module}.{fn}", fn, fp, 5, fan_in=fan_in, fan_out=fan_out))
        edges.append(_contains(module, f"{module}.{fn}", fp))

    for i in range(len(stages) - 1):
        src_mod, _, src_fn, _ = stages[i]
        tgt_mod, _, tgt_fn, _ = stages[i + 1]
        edges.append(_imports(src_mod, tgt_mod))
        edges.append(_calls(f"{src_mod}.{src_fn}", f"{tgt_mod}.{tgt_fn}"))

    return nodes, edges


def build_star() -> tuple[list[GraphDict], list[GraphDict]]:
    """One orchestrator dispatches to N independent leaf services.

    orchestrator ──► auth_service
                ──► payment_service
                ──► notification_service
                ──► audit_service
    """
    leaves = ["auth_service", "payment_service", "notification_service", "audit_service"]
    nodes: list[GraphDict] = []
    edges: list[GraphDict] = []

    # orchestrator
    orch_fp = "orchestrator.py"
    nodes.append(_file_node("orchestrator", orch_fp, 40))
    orch_fn = "orchestrator.run_workflow"
    nodes.append(_func_node(orch_fn, "run_workflow", orch_fp, 5, fan_in=0, fan_out=len(leaves)))
    edges.append(_contains("orchestrator", orch_fn, orch_fp))

    for leaf in leaves:
        fp = f"{leaf}.py"
        nodes.append(_file_node(leaf, fp))
        fn = f"{leaf}.execute"
        nodes.append(_func_node(fn, "execute", fp, 5, fan_in=1, fan_out=0))
        edges.append(_contains(leaf, fn, fp))
        edges.append(_imports("orchestrator", leaf))
        edges.append(_calls(orch_fn, fn))

    return nodes, edges


def build_dag() -> tuple[list[GraphDict], list[GraphDict]]:
    """Clean layered DAG: api → service → repo → db_client.

    api_layer ──► user_service ──► user_repo ──► db_client
              ──► order_service ──► order_repo ─┘
    """
    nodes: list[GraphDict] = []
    edges: list[GraphDict] = []

    layers = [
        # (module, file, func, fan_in, fan_out)
        ("db_client", "db_client.py", "execute_query", 2, 0),
        ("user_repo", "user_repo.py", "find_user", 1, 1),
        ("order_repo", "order_repo.py", "find_order", 1, 1),
        ("user_service", "user_service.py", "get_user", 1, 1),
        ("order_service", "order_service.py", "get_order", 1, 1),
        ("api_layer", "api_layer.py", "handle_request", 0, 2),
    ]

    for module, fp, fn, fi, fo in layers:
        nodes.append(_file_node(module, fp))
        nodes.append(_func_node(f"{module}.{fn}", fn, fp, 5, fan_in=fi, fan_out=fo))
        edges.append(_contains(module, f"{module}.{fn}", fp))

    # repo → db_client
    for repo in ("user_repo", "order_repo"):
        edges.append(_imports(repo, "db_client"))
        edges.append(
            _calls(
                f"{repo}.{'find_user' if repo == 'user_repo' else 'find_order'}",
                "db_client.execute_query",
            )
        )

    # service → repo
    edges.append(_imports("user_service", "user_repo"))
    edges.append(_calls("user_service.get_user", "user_repo.find_user"))
    edges.append(_imports("order_service", "order_repo"))
    edges.append(_calls("order_service.get_order", "order_repo.find_order"))

    # api → services
    edges.append(_imports("api_layer", "user_service"))
    edges.append(_imports("api_layer", "order_service"))
    edges.append(_calls("api_layer.handle_request", "user_service.get_user"))
    edges.append(_calls("api_layer.handle_request", "order_service.get_order"))

    return nodes, edges


# ── namespace prefix so multiple patterns don't collide in "all" mode ─────────


def _prefix(
    nodes: list[GraphDict], edges: list[GraphDict], ns: str
) -> tuple[list[GraphDict], list[GraphDict]]:
    def p(fqn: str) -> str:
        return f"{ns}.{fqn}"

    new_nodes = [{**n, "id": p(n["id"]), "file_path": f"{ns}/{n['file_path']}"} for n in nodes]
    # Reconstruct edge ID from the prefixed source and target to keep
    # ID consistent with the {source}:{type}:{target} contract in _edge().
    new_edges: list[GraphDict] = []
    for e in edges:
        ns_source = p(e["source"])
        ns_target = p(e["target"])
        new_edges.append(
            {
                **e,
                "id": f"{ns_source}:{e['type'].lower()}:{ns_target}",
                "source": ns_source,
                "target": ns_target,
                "file_path": f"{ns}/{e['file_path']}" if e["file_path"] else "",
            }
        )
    return new_nodes, new_edges


PATTERNS: dict[str, Any] = {
    "hub": build_hub,
    "chain": build_chain,
    "star": build_star,
    "dag": build_dag,
}


def generate(pattern: str) -> dict[str, Any]:
    """Return a graph dict for the given pattern (or 'all' to combine all four)."""
    if pattern == "all":
        all_nodes: list[GraphDict] = []
        all_edges: list[GraphDict] = []
        for name, builder in PATTERNS.items():
            pat_nodes, pat_edges = builder()
            ns_nodes, ns_edges = _prefix(pat_nodes, pat_edges, name)
            all_nodes.extend(ns_nodes)
            all_edges.extend(ns_edges)
        nodes, edges = all_nodes, all_edges
        source = "ideal:all"
    else:
        if pattern not in PATTERNS:
            msg = f"Unknown pattern '{pattern}'. Choose from: {', '.join(PATTERNS)} or 'all'."
            raise ValueError(msg)
        nodes, edges = PATTERNS[pattern]()
        source = f"ideal:{pattern}"

    return {
        "metadata": {
            "source_path": source,
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
        "nodes": nodes,
        "edges": edges,
    }


# Pattern name → existing builder function.
# Patterns not listed here fall back to build_hub as a generic scaffold.
_PATTERN_TO_BUILDER: dict[str, Any] = {
    "pure_utility": build_hub,
    "pipeline_stage": build_chain,
    "orchestrator": build_star,
    "layered_dag": build_dag,
    "dispatcher": build_star,
}


def generate_from_ontology(patterns_path: str) -> dict[str, Any]:
    """Generate an ideal graph from a patterns.yaml file.

    For each project_domain, instantiates the expected_pattern template
    using the real fqn_prefix as the node namespace.
    """
    path = Path(patterns_path)
    if not path.is_file():
        msg = f"Patterns file not found: {patterns_path}"
        raise FileNotFoundError(msg)
    content = yaml.safe_load(path.read_text())
    raw: dict[str, Any] = content if isinstance(content, dict) else {}
    domains: list[dict[str, Any]] = raw.get("project_domains") or []

    all_nodes: list[GraphDict] = []
    all_edges: list[GraphDict] = []

    for domain in domains:
        fqn_prefix: str = domain["fqn_prefix"]
        pattern_name: str = domain["expected_pattern"]
        builder = _PATTERN_TO_BUILDER.get(pattern_name, build_hub)
        pat_nodes, pat_edges = builder()
        ns_nodes, ns_edges = _prefix(pat_nodes, pat_edges, fqn_prefix)
        all_nodes.extend(ns_nodes)
        all_edges.extend(ns_edges)

    return {
        "metadata": {
            "source_path": patterns_path,
            "node_count": len(all_nodes),
            "edge_count": len(all_edges),
        },
        "nodes": all_nodes,
        "edges": all_edges,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point for the ideal graph generator script."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--pattern",
        "-p",
        choices=[*PATTERNS, "all"],
        default=None,
        help="Graph pattern to generate.",
    )
    parser.add_argument(
        "--from-ontology",
        metavar="PATTERNS_YAML",
        default=None,
        help="Generate ideal graph from a patterns.yaml file (project_domains).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="-",
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()

    if args.from_ontology:
        graph = generate_from_ontology(args.from_ontology)
    elif args.pattern:
        graph = generate(args.pattern)
    else:
        graph = generate("all")

    if args.output == "-":
        json.dump(graph, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        Path(args.output).write_text(json.dumps(graph, indent=2) + "\n")
        node_count = graph["metadata"]["node_count"]
        edge_count = graph["metadata"]["edge_count"]
        print(f"Written {node_count} nodes, {edge_count} edges → {args.output}")


if __name__ == "__main__":
    main()
