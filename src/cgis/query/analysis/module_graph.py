"""How one package is built inside: its modules and the dependencies between them (#741).

Nodes are the package's direct children (each sub-package one node) or, with
``expand``, every file below it. Edges are module-to-module dependencies by
kind: ``imports``, ``extends`` and ``calls``. A symbol-level edge counts for the
file that holds each end. Test files are left out by default, type-only imports
always (as the cycle tools do, #499). Each kind is transitively reduced on its
own, so the picture shows the direct layering rather than every implied edge;
dependencies on code outside the package are summarised per module, not drawn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cgis.core.models import EdgeType, NodeType
from cgis.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from cgis.core.models import Edge, Node

#: Edge kinds by name, in the order a reader takes them in. `from pkg import mod`
#: records IMPORTS to `pkg` and IMPORTS_SYMBOL to `pkg.mod`; the second is the
#: module dependency, so imports read both.
KINDS: dict[str, frozenset[EdgeType]] = {
    "imports": frozenset({EdgeType.IMPORTS, EdgeType.IMPORTS_SYMBOL}),
    "extends": frozenset({EdgeType.EXTENDS}),
    "calls": frozenset({EdgeType.CALLS}),
}
#: What a picture shows unless asked otherwise: calls make it much denser.
DEFAULT_LAYERS: tuple[str, ...] = ("imports", "extends")

_FILE_TYPES = frozenset({NodeType.FILE, NodeType.MODULE})
_ARROWS = {"imports": "-->", "extends": "==>", "calls": "-.->"}
_NON_ID = re.compile(r"[^0-9A-Za-z]+")


@dataclass(frozen=True)
class ModuleEdge:
    """One dependency of ``source`` on ``target``, both nodes of the module graph."""

    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class ModuleGraph:
    """A package's module graph.

    ``dropped`` counts, per kind, the edges the transitive reduction removed;
    ``outside`` maps a module to the packages outside ``prefix`` it depends on.
    """

    prefix: str
    nodes: list[str]
    subpackages: list[str]
    edges: list[ModuleEdge]
    dropped: dict[str, int]
    outside: dict[str, list[str]]


def _under(fqn: str, prefix: str) -> bool:
    return fqn == prefix or fqn.startswith(prefix + ".")


def transitive_reduction(edges: Iterable[ModuleEdge]) -> list[ModuleEdge]:
    """Drop each edge implied by a longer path, keeping every edge of a cycle.

    ``u -> v`` stays when ``u`` is reachable from ``v`` (the edge is part of a
    cycle, and dropping it could make the cycle vanish from the picture) or when
    ``v`` is unreachable from ``u`` once the edge itself is removed. Input order
    is preserved.
    """
    edge_list = list(edges)
    succ: dict[str, set[str]] = {}
    for e in edge_list:
        succ.setdefault(e.source, set()).add(e.target)

    def reaches(start: str, goal: str, without: tuple[str, str] | None = None) -> bool:
        stack, seen = [start], {start}
        while stack:
            node = stack.pop()
            for nxt in succ.get(node, ()):
                if (node, nxt) == without or nxt in seen:
                    continue
                if nxt == goal:
                    return True
                seen.add(nxt)
                stack.append(nxt)
        return False

    return [
        e
        for e in edge_list
        if reaches(e.target, e.source) or not reaches(e.source, e.target, (e.source, e.target))
    ]


def _file_index(nodes: Sequence[Node]) -> tuple[dict[str, str], set[str], set[str]]:
    """Per node id, the id of the file that holds it; the test files; the package files."""
    by_path = {n.file_path: n.id for n in nodes if n.type in _FILE_TYPES and n.file_path}
    holder = {n.id: by_path[n.file_path] for n in nodes if n.file_path in by_path}
    tests = {n.id for n in nodes if n.type in _FILE_TYPES and n.is_test}
    ids = set(by_path.values())
    # A file is a package when another file's id continues its own.
    packages = {f.rsplit(".", 1)[0] for f in ids if "." in f} & ids
    return holder, tests, packages


def _group(file_id: str, prefix: str, expand: bool) -> str:
    """The node a file belongs to: itself, or the package's direct child holding it."""
    if expand or not file_id.startswith(prefix + "."):
        return file_id
    return f"{prefix}.{file_id[len(prefix) + 1 :].split('.', 1)[0]}"


def _outside_package(file_id: str, packages: set[str]) -> str:
    """The package a file outside the analysed one belongs to: itself, if it is one."""
    if file_id in packages or "." not in file_id:
        return file_id
    return file_id.rsplit(".", 1)[0]


def build_module_graph(
    nodes: Sequence[Node],
    edges: Sequence[Edge],
    prefix: str,
    layers: Sequence[str] = DEFAULT_LAYERS,
    *,
    reduce: bool = True,
    exclude_tests: bool = True,
    expand: bool = False,
) -> ModuleGraph:
    """The module graph of ``prefix`` from an already-loaded node and edge list."""
    unknown = [layer for layer in layers if layer not in KINDS]
    if unknown:
        msg = f"unknown layer(s) {unknown}; choose from {list(KINDS)}"
        raise ValueError(msg)
    holder, tests, packages = _file_index(nodes)
    inside = sorted(
        f
        for f in set(holder.values())
        if _under(f, prefix) and f != prefix and not (exclude_tests and f in tests)
    )
    members = sorted({_group(f, prefix, expand) for f in inside})
    subpackages = (
        [] if expand else [m for m in members if any(f.startswith(m + ".") for f in inside)]
    )
    member_set = set(members)
    wanted = {edge_type: layer for layer in layers for edge_type in KINDS[layer]}
    inside_set = set(inside)
    found: dict[tuple[str, str, str], None] = {}
    outside: dict[str, set[str]] = {}
    for e in edges:
        kind = wanted.get(e.type)
        if kind is None or e.type_only:
            continue
        src_file, dst_file = holder.get(e.source), holder.get(e.target)
        if src_file is None or dst_file is None or src_file not in inside_set:
            continue
        src = _group(src_file, prefix, expand)
        if _under(dst_file, prefix):
            if exclude_tests and dst_file in tests:
                continue
            dst = _group(dst_file, prefix, expand)
            if dst in member_set and dst != src:
                found[(src, dst, kind)] = None
        else:
            outside.setdefault(src, set()).add(_outside_package(dst_file, packages))
    all_edges = [ModuleEdge(s, t, k) for s, t, k in found]
    kept: list[ModuleEdge] = []
    dropped: dict[str, int] = {}
    for layer in layers:
        layer_edges = [e for e in all_edges if e.kind == layer]
        reduced = transitive_reduction(layer_edges) if reduce else layer_edges
        dropped[layer] = len(layer_edges) - len(reduced)
        kept.extend(reduced)
    return ModuleGraph(
        prefix=prefix,
        nodes=members,
        subpackages=subpackages,
        edges=sorted(kept, key=lambda e: (e.kind, e.source, e.target)),
        dropped=dropped,
        outside={m: sorted(pkgs) for m, pkgs in sorted(outside.items())},
    )


def module_graph(
    db_path: str,
    prefix: str,
    layers: Sequence[str] = DEFAULT_LAYERS,
    *,
    reduce: bool = True,
    exclude_tests: bool = True,
    expand: bool = False,
) -> ModuleGraph:
    """The module graph of the package at ``prefix`` in the graph at ``db_path``.

    ``prefix`` is matched on whole dot-segments, as given: the surfaces resolve a
    prefix that omits the ingest root first (``effective_prefixes``, #584).

    Raises:
        FileNotFoundError: if ``db_path`` is not an existing file (run ingest first).
    """
    if not Path(db_path).is_file():
        msg = f"Graph database not found: {db_path}"
        raise FileNotFoundError(msg)
    with SQLiteStore(db_path) as store:
        return build_module_graph(
            store.get_all_nodes(),
            store.get_all_edges(),
            prefix.strip(),
            layers,
            reduce=reduce,
            exclude_tests=exclude_tests,
            expand=expand,
        )


def _short(name: str, prefix: str) -> str:
    return name[len(prefix) + 1 :] if name.startswith(prefix + ".") else name


def _node_id(name: str, prefix: str) -> str:
    return "m_" + (_NON_ID.sub("_", _short(name, prefix)).strip("_") or "root")


def render_mermaid(graph: ModuleGraph) -> str:
    """The graph as a Mermaid flowchart, with outbound dependencies as a table under it.

    Imports are ``-->``, inheritance ``==>`` and calls ``-.->``. Node ids carry an
    ``m_`` prefix, so a module named ``end`` or ``graph`` is not a keyword.
    """
    lines = ["```mermaid", "flowchart TD"]
    for name in graph.nodes:
        label = _short(name, graph.prefix)
        if name in graph.subpackages:
            lines.append(f'    {_node_id(name, graph.prefix)}[["{label}/"]]')
        else:
            lines.append(f'    {_node_id(name, graph.prefix)}["{label}"]')
    for e in graph.edges:
        src, dst = _node_id(e.source, graph.prefix), _node_id(e.target, graph.prefix)
        lines.append(f"    {src} {_ARROWS[e.kind]} {dst}")
    lines.append("```")
    kinds = ", ".join(f"{k} {_ARROWS[k]}" for k in graph.dropped)
    removed = ", ".join(f"{k} {n}" for k, n in graph.dropped.items() if n)
    lines.append("")
    lines.append(f"Edges: {kinds}." + (f" Implied edges dropped: {removed}." if removed else ""))
    if graph.outside:
        lines += ["", f"| module | reaches outside `{graph.prefix}` |", "|---|---|"]
        for module, packages in graph.outside.items():
            lines.append(f"| {_short(module, graph.prefix)} | {', '.join(packages)} |")
    return "\n".join(lines)


def graph_to_dict(graph: ModuleGraph) -> dict[str, object]:
    """The graph as plain data: ``{prefix, nodes, subpackages, edges, dropped, outside}``."""
    return {
        "prefix": graph.prefix,
        "nodes": graph.nodes,
        "subpackages": graph.subpackages,
        "edges": [{"source": e.source, "target": e.target, "kind": e.kind} for e in graph.edges],
        "dropped": graph.dropped,
        "outside": graph.outside,
    }
