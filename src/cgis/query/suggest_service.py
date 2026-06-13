"""Package-cohesion orchestration shared by the CLI and the MCP server (#242)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cgis.core.models import Edge, Node

from cgis.query.cohesion import (
    THRESHOLDS,
    build_file_graph,
    classify_verdict,
    greedy_modularity,
    layout_direction,
    partition_divergence,
)
from cgis.storage.sqlite_store import SQLiteStore

_ROOT_GROUP = "<root>"


@dataclass(frozen=True)
class Community:
    """One detected community: an id and its member files (last FQN segment)."""

    id: int
    files: list[str]


@dataclass(frozen=True)
class Bridge:
    """A cross-community edge — the cost of splitting (file names, last segment)."""

    source: str
    target: str
    weight: float


@dataclass(frozen=True)
class SuggestReport:
    """Full suggest-packages result; serialized verbatim to CLI-json and MCP."""

    package: str
    layer: str
    file_count: int
    edge_count: int
    modularity_q: float
    divergence: float
    direction: str
    verdict: str
    communities: list[Community]
    bridges: list[Bridge]
    thresholds: dict[str, float]
    note: str | None = None


def _leaf(fqn: str) -> str:
    """Return the last FQN segment (the module name) for readable output."""
    return fqn.rsplit(".", 1)[-1]


def _dir_group(fqn: str, prefix: str) -> str:
    """Return the file's directory group relative to the package root.

    A single remaining segment after the prefix maps to the shared ``"<root>"``
    group; two or more map to the first remaining segment (a real sub-directory).
    """
    remainder = fqn[len(prefix) + 1 :] if fqn.startswith(prefix + ".") else fqn
    parts = remainder.split(".")
    return _ROOT_GROUP if len(parts) <= 1 else parts[0]


def _empty_report(package: str, layer: str, note: str) -> SuggestReport:
    """Return a no_signal report carrying a diagnostic note (never a silent green)."""
    return SuggestReport(
        package=package,
        layer=layer,
        file_count=0,
        edge_count=0,
        modularity_q=0.0,
        divergence=0.0,
        direction="matched",
        verdict="no_signal",
        communities=[],
        bridges=[],
        thresholds=dict(THRESHOLDS),
        note=note,
    )


def suggest_packages(
    db_path: str, prefix: str, with_calls: bool = False, min_q: float = 0.35
) -> SuggestReport:
    """Detect a package's communities and score layout divergence (#242).

    Raises:
        FileNotFoundError: if ``db_path`` is not an existing file (run ingest first).
    """
    if not Path(db_path).is_file():
        msg = f"Graph database not found: {db_path}"
        raise FileNotFoundError(msg)

    layer = "imports+calls" if with_calls else "imports"
    with SQLiteStore(db_path) as store:
        nodes: list[Node] = store.get_all_nodes()
        edges: list[Edge] = store.get_all_edges()

    graph = build_file_graph(nodes, edges, prefix, with_calls)
    if not graph.files:
        return _empty_report(prefix, layer, f"fqn_prefix '{prefix}' matched 0 nodes")

    internal_edges = sum(len(v) for v in graph.adj.values()) // 2
    if internal_edges == 0:
        had_import_attempts = any(
            e.source.startswith(prefix + ".") or e.source == prefix
            for e in edges
            if e.type.value == "IMPORTS"
        )
        note = (
            f"{prefix}: files found but no import resolves inside the package — the "
            "graph looks mis-rooted or imports are unresolved; try ingesting the "
            "package's parent directory"
            if had_import_attempts
            else f"{prefix}: no intra-package imports (a flat leaf bag)"
        )
        return _empty_report(prefix, layer, note)

    communities, q = greedy_modularity(graph)
    comm_of = {f: i for i, c in enumerate(communities) for f in c}
    dir_of = {f: _dir_group(f, prefix) for f in graph.files}
    divergence = partition_divergence(comm_of, dir_of)
    direction = layout_direction(comm_of, dir_of)
    thresholds = {**THRESHOLDS, "split": min_q}
    verdict = classify_verdict(q=q, d=divergence, direction=direction, thresholds=thresholds)

    bridges = sorted(
        (
            Bridge(source=_leaf(a), target=_leaf(b), weight=w)
            for a in graph.adj
            for b, w in graph.adj[a].items()
            if a < b and comm_of[a] != comm_of[b]
        ),
        key=lambda br: (-br.weight, br.source, br.target),
    )
    return SuggestReport(
        package=prefix,
        layer=layer,
        file_count=len(graph.files),
        edge_count=internal_edges,
        modularity_q=round(q, 4),
        divergence=round(divergence, 4),
        direction=direction,
        verdict=verdict,
        communities=[
            Community(id=i, files=[_leaf(f) for f in c]) for i, c in enumerate(communities)
        ],
        bridges=bridges,
        thresholds=thresholds,
    )


def report_to_dict(report: SuggestReport) -> dict[str, object]:
    """Return a plain-dict view for JSON (CLI --format json and MCP share this)."""
    return asdict(report)
