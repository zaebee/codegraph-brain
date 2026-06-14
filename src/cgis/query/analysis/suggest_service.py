"""Package-cohesion orchestration shared by the CLI and the MCP server (#242)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cgis.core.models import EdgeType

if TYPE_CHECKING:
    from cgis.core.models import Edge, Node

from cgis.query.analysis.cohesion import (
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
    The package node itself (``fqn == prefix``, e.g. an ``__init__``) is ``<root>``.
    """
    if fqn == prefix:
        return _ROOT_GROUP
    remainder = fqn[len(prefix) + 1 :] if fqn.startswith(prefix + ".") else fqn
    parts = remainder.split(".")
    return _ROOT_GROUP if len(parts) <= 1 else parts[0]


def _empty_report(package: str, layer: str, note: str, file_count: int = 0) -> SuggestReport:
    """Return a no_signal report carrying a diagnostic note (never a silent green).

    ``file_count`` is passed through for the mis-rooted / flat-leaf-bag cases —
    files WERE found, there were just no intra-package edges to score, so a JSON
    consumer should see the real count, not a misleading 0.
    """
    return SuggestReport(
        package=package,
        layer=layer,
        file_count=file_count,
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
    db_path: str, prefix: str | None, with_calls: bool = False, min_q: float = 0.35
) -> SuggestReport:
    """Detect a package's communities and score layout divergence (#242).

    ``prefix`` is normalized once here: a ``None`` or blank value (a CLI/MCP
    client may send either) collapses to a ``no_signal`` report rather than a
    crash, so every downstream helper receives a clean non-empty string.

    Raises:
        FileNotFoundError: if ``db_path`` is not an existing file (run ingest first).
    """
    if not Path(db_path).is_file():
        msg = f"Graph database not found: {db_path}"
        raise FileNotFoundError(msg)

    layer = "imports+calls" if with_calls else "imports"
    package = (prefix or "").strip()
    if not package:
        return _empty_report("", layer, "no fqn_prefix given")

    with SQLiteStore(db_path) as store:
        nodes: list[Node] = store.get_all_nodes()
        edges: list[Edge] = store.get_all_edges()

    graph = build_file_graph(nodes, edges, package, with_calls)
    if not graph.files:
        return _empty_report(package, layer, f"fqn_prefix '{package}' matched 0 nodes")

    internal_edges = sum(len(v) for v in graph.adj.values()) // 2
    if internal_edges == 0:
        had_import_attempts = any(
            e.source.startswith(package + ".") or e.source == package
            for e in edges
            if e.type == EdgeType.IMPORTS
        )
        note = (
            f"{package}: files found but no import resolves inside the package — the "
            "graph looks mis-rooted or imports are unresolved; try ingesting the "
            "package's parent directory"
            if had_import_attempts
            else f"{package}: no intra-package imports (a flat leaf bag)"
        )
        return _empty_report(package, layer, note, file_count=len(graph.files))

    communities, q = greedy_modularity(graph)
    comm_of = {f: i for i, c in enumerate(communities) for f in c}
    dir_of = {f: _dir_group(f, package) for f in graph.files}
    # Clamp to [0, 1]: NMI is mathematically in range, but float error can leak
    # a tiny negative / >1 value that looks odd in JSON.
    divergence = max(0.0, min(1.0, partition_divergence(comm_of, dir_of)))
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
        package=package,
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
