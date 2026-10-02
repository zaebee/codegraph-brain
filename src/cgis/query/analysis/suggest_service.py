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
    children_graph,
    classify_verdict,
    direct_child,
    greedy_modularity,
    layout_direction,
    partition_divergence,
)
from cgis.storage.sqlite_store import SQLiteStore

_ROOT_GROUP = "<root>"


@dataclass(frozen=True)
class Community:
    """One detected community: an id and its members.

    By default the members are the package's direct children — `analysis`, a
    sub-package, or `engine`, a module. With `all_descendants` they are files,
    named by their path under the analysed package — `analysis.analyzer`. Either
    way a name is the full FQN where the relative one would be ambiguous; see
    `_member_names`.
    """

    id: int
    files: list[str]


@dataclass(frozen=True)
class Bridge:
    """A cross-community edge — the cost of splitting.

    Endpoints are named exactly as community members are, so the two lists can be
    read against each other.
    """

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
    # Fraction of files with at least one intra-package edge. A split is only
    # trusted above thresholds["min_connected"] — below it, a high Q is a
    # sparse-graph artifact (most files are independent). 0.0 for no_signal.
    connected_fraction: float = 0.0
    note: str | None = None
    # What the members are: "children" (the package's direct children, each
    # sub-package one node — the default) or "files" (every file below it).
    level: str = "children"


def _member_names(file_ids: tuple[str, ...], prefix: str) -> dict[str, str]:
    """Map every file under `prefix` to a display name, unique across the report.

    Uniqueness is a property of the whole set, not of each name, so it is decided
    once here rather than argued per row. Two earlier attempts each fixed one
    shape and left another (#446, #447 review):

    * the last FQN segment collided for `p/sub/` against `p/sub/sub.py`;
    * the path under the prefix collided for the package's own node against a
      module named after it — `p` and `p.p` both render `p`.

    So: members are named by their path under the prefix, the package's own node
    by its full FQN, and if those still clash — only possible when a module is
    named exactly after its package — the whole report falls back to full FQNs.
    Degrading the entire table keeps one rule visible in the output instead of
    one row spelled differently from its neighbours for reasons the reader
    cannot see.

    Every name maps back to a node id: relative ones by joining the prefix, and
    absolute ones as they stand. That matters because this tool is MCP-facing —
    an agent reads a community, picks a member and asks about it — and it is why
    `__init__` was wrong: no such file exists in a TypeScript package, where the
    extractor folds `/index` just as Python folds `/__init__`, and
    `prefix + ".__init__"` names nothing in either.
    """
    absolute = {fid: fid for fid in file_ids}
    if not prefix:
        return absolute

    relative = {fid: fid if fid == prefix else fid[len(prefix) + 1 :] for fid in file_ids}
    return relative if len(set(relative.values())) == len(relative) else absolute


def _dir_groups(file_ids: tuple[str, ...], prefix: str) -> dict[str, str]:
    """Map every file under ``prefix`` to its directory group relative to the root.

    A file two or more segments below the prefix belongs to the sub-package that
    holds it, named by its FQN. A sub-package's own node (``p.sub`` for ``p/sub/__init__``)
    is one segment below, but it is that directory's file, not a root module, so it
    joins the sub-package's group too (#446) — otherwise every ``__init__`` sat in
    ``<root>`` and inflated the divergence of a package whose directories already
    matched its communities. The package node itself (``fqn == prefix``) and plain
    root modules are ``<root>``.
    """
    children = [(fid, direct_child(fid, prefix)) for fid in file_ids]
    sub_dirs = {child for fid, child in children if child != fid}
    return {fid: child if child in sub_dirs else _ROOT_GROUP for fid, child in children}


def _empty_report(
    package: str, layer: str, note: str, file_count: int = 0, level: str = "children"
) -> SuggestReport:
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
        level=level,
    )


def suggest_packages(
    db_path: str,
    prefix: str | None,
    with_calls: bool = False,
    min_q: float = 0.35,
    all_descendants: bool = False,
) -> SuggestReport:
    """Detect a package's communities and score layout divergence (#242).

    By default the members are the package's **direct children**: each
    sub-package is one node carrying the sum of its files' edges, so the verdict
    answers "should these children be regrouped" (#446). Their layout is flat
    by construction, so on a package with sub-packages the result is ``split``,
    ``borderline`` or ``leave``. ``all_descendants`` puts every file below the
    package into the graph instead and compares the communities with the
    sub-directories, which answers "how do all these files cluster".

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

    level = "files" if all_descendants else "children"
    graph = build_file_graph(nodes, edges, package, with_calls)
    if not graph.files:
        return _empty_report(package, layer, f"fqn_prefix '{package}' matched 0 nodes", level=level)

    if len(graph.files) < 2:
        # A single matched file is a module (or a one-file package), not something
        # to split. Guard BEFORE the no-internal-edges check below — otherwise the
        # lone module's outbound imports trip the 'mis-rooted' diagnostic falsely
        # (it has import edges, none of which can resolve inside a 1-file set).
        return _empty_report(
            package,
            layer,
            f"'{package}' matched a single module, not a multi-file package — nothing to split",
            file_count=len(graph.files),
            level=level,
        )

    file_graph = graph
    if not all_descendants:
        graph = children_graph(file_graph, package)
        if len(graph.files) < 2:
            return _empty_report(
                package,
                layer,
                f"'{package}' has a single child, {graph.files[0]} — analyse that, or pass "
                "all_descendants (--all-descendants) to cluster every file below the package",
                file_count=len(graph.files),
                level=level,
            )

    internal_edges = sum(len(v) for v in graph.adj.values()) // 2
    if internal_edges == 0 and file_graph.adj:
        return _empty_report(
            package,
            layer,
            f"{package}: every intra-package import stays inside a sub-package, so nothing "
            "links the children — analyse a sub-package, or pass all_descendants "
            "(--all-descendants)",
            file_count=len(graph.files),
            level=level,
        )
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
        return _empty_report(package, layer, note, file_count=len(graph.files), level=level)

    communities, q = greedy_modularity(graph)
    comm_of = {f: i for i, c in enumerate(communities) for f in c}
    dir_of = _dir_groups(graph.files, package)
    # Clamp to [0, 1]: NMI is mathematically in range, but float error can leak
    # a tiny negative / >1 value that looks odd in JSON.
    divergence = max(0.0, min(1.0, partition_divergence(comm_of, dir_of)))
    direction = layout_direction(comm_of, dir_of)
    thresholds = {**THRESHOLDS, "split": min_q}
    verdict = classify_verdict(q=q, d=divergence, direction=direction, thresholds=thresholds)

    # Sparse-graph guard: modularity Q is unreliable when most files have no
    # intra-package edge — a single tiny cluster then inflates Q on a package of
    # otherwise-independent helpers (owner-api/utils: 3 edges over 12 files, Q=0.44,
    # 58% isolated → a false 'split'). Only act on it when enough files are coupled.
    # build_file_graph only adds non-empty adjacency entries (keyed by files under
    # the prefix), so adj keys ARE exactly the files with an intra-package edge.
    connected_fraction = len(graph.adj) / len(graph.files)
    sparse_note: str | None = None
    if verdict in ("split", "borderline") and connected_fraction < thresholds["min_connected"]:
        sparse_note = (
            f"only {len(graph.adj)}/{len(graph.files)} ({connected_fraction:.0%}) of files "
            f"are coupled to a sibling — mostly independent helpers, so the Q={q:.2f} is a "
            "sparse-graph artifact, not real community structure; nothing to split"
        )
        verdict = "leave"

    names = _member_names(graph.files, package)
    bridges = sorted(
        (
            Bridge(source=names[a], target=names[b], weight=w)
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
            Community(id=i, files=[names[f] for f in c]) for i, c in enumerate(communities)
        ],
        bridges=bridges,
        thresholds=thresholds,
        connected_fraction=round(connected_fraction, 4),
        note=sparse_note,
        level=level,
    )


def report_to_dict(report: SuggestReport) -> dict[str, object]:
    """Return a plain-dict view for JSON (CLI --format json and MCP share this)."""
    return asdict(report)
