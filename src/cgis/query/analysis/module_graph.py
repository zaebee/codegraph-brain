"""How one package is built inside: its modules and the dependencies between them (#741).

Nodes are the package's direct children (each sub-package one node) or, with
``expand``, every file below it. Edges are module-to-module dependencies by
kind: ``imports``, ``extends`` and ``calls``. A symbol-level edge counts for the
file that holds each end. Test files are left out by default, type-only imports
always (as the cycle tools do, #499). In Go a FILE node is one file of a
package and an import names one of the package's files, so ``expand`` there
shows files, not packages; the collapsed view and the outside table do not
depend on which file. Each kind is transitively reduced on its
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
    from collections.abc import Hashable, Iterable, Mapping, Sequence

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
    ``covered`` the imports left out because a drawn inheritance edge joins the
    same pair, so that ``==>`` also stands for the import;
    ``outside`` maps a module to the packages outside ``prefix`` it depends on.
    """

    prefix: str
    nodes: list[str]
    subpackages: list[str]
    edges: list[ModuleEdge]
    dropped: dict[str, int]
    outside: dict[str, list[str]]
    covered: int = 0


def _under(fqn: str, prefix: str) -> bool:
    return fqn == prefix or fqn.startswith(prefix + ".")


def _reach[N: Hashable](succ: Mapping[N, set[N]], starts: Iterable[N]) -> set[N]:
    """``starts`` and every node reachable from them, in one walk."""
    stack = list(starts)
    seen = set(stack)
    while stack:
        for nxt in succ.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _condense(
    edges: list[ModuleEdge],
) -> tuple[dict[str, frozenset[str]], dict[frozenset[str], set[frozenset[str]]]]:
    """Each node's strongly connected component, and the DAG between components."""
    succ: dict[str, set[str]] = {}
    for e in edges:
        succ.setdefault(e.source, set()).add(e.target)
        succ.setdefault(e.target, set())
    reach = {n: _reach(succ, succ[n]) for n in succ}
    comp = {n: frozenset({n} | {m for m in reach[n] if n in reach[m]}) for n in succ}
    dag: dict[frozenset[str], set[frozenset[str]]] = {}
    for e in edges:
        if comp[e.source] != comp[e.target]:
            dag.setdefault(comp[e.source], set()).add(comp[e.target])
    return comp, dag


def _implied(
    dag: dict[frozenset[str], set[frozenset[str]]], a: frozenset[str], b: frozenset[str]
) -> bool:
    """Is component ``b`` reachable from ``a`` through some third component."""
    return b in _reach(dag, [c for c in dag.get(a, ()) if c != b])


def transitive_reduction(edges: Iterable[ModuleEdge]) -> list[ModuleEdge]:
    """Drop each edge implied by a longer path, keeping every edge of a cycle.

    Reduced on the condensation, each strongly connected component one node.
    Every edge inside a component stays: dropping one could make a cycle
    vanish from the picture. The edges joining two components, however many
    module pairs carry them, go together or stay together: they go only when
    the target component is reachable from the source component through a
    third. Input order is preserved.
    """
    edge_list = list(edges)
    comp, dag = _condense(edge_list)
    return [
        e
        for e in edge_list
        if comp[e.source] == comp[e.target] or not _implied(dag, comp[e.source], comp[e.target])
    ]


def _file_index(nodes: Sequence[Node]) -> tuple[dict[str, str], set[str], set[str]]:
    """Per node id, the id of the file that holds it; the test files; the package files."""
    by_path: dict[str, str] = {}
    tests: set[str] = set()
    for n in nodes:
        if n.type in _FILE_TYPES:
            if n.file_path:
                by_path[n.file_path] = n.id
            if n.is_test:
                tests.add(n.id)
    holder = {n.id: by_path[n.file_path] for n in nodes if n.file_path in by_path}
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


def checked_layers(layers: Sequence[str]) -> list[str]:
    """The layers asked for, each once, in order; an empty or unknown one is an error."""
    unique = list(dict.fromkeys(layers))
    if not unique:
        msg = f"no layer given; choose from {list(KINDS)}"
        raise ValueError(msg)
    unknown = [layer for layer in unique if layer not in KINDS]
    if unknown:
        msg = f"unknown layer(s) {unknown}; choose from {list(KINDS)}"
        raise ValueError(msg)
    return unique


def _imported_through(edges: Sequence[Edge], holder: dict[str, str]) -> dict[str, set[str]]:
    """Per file, the files whose names it imports (IMPORTS_SYMBOL), for the expand view.

    `from pkg.sub import mod` records IMPORTS to pkg.sub as well as IMPORTS_SYMBOL
    to pkg.sub.mod. Drawn per module, the first would put an edge on every
    package __init__ its submodules are imported through.
    """
    through: dict[str, set[str]] = {}
    for e in edges:
        if e.type == EdgeType.IMPORTS_SYMBOL:
            src_file, dst_file = holder.get(e.source), holder.get(e.target)
            if src_file is not None and dst_file is not None:
                through.setdefault(src_file, set()).add(dst_file)
    return through


@dataclass(frozen=True)
class _Scope:
    """What decides whether an edge belongs to the picture, and where it lands."""

    prefix: str
    expand: bool
    holder: dict[str, str]
    skipped: set[str]
    packages: set[str]
    inside: set[str]
    members: set[str]
    through: dict[str, set[str]]


def _ends(e: Edge, scope: _Scope) -> tuple[str, str] | None:
    """The source member and the target file of an edge the picture uses, or None."""
    src_file, dst_file = scope.holder.get(e.source), scope.holder.get(e.target)
    if src_file not in scope.inside or dst_file is None or dst_file in scope.skipped:
        return None
    if e.type == EdgeType.IMPORTS and any(
        f.startswith(dst_file + ".") for f in scope.through.get(src_file, ())
    ):
        return None
    return _group(src_file, scope.prefix, scope.expand), dst_file


def _uncovered(kept: list[ModuleEdge]) -> tuple[list[ModuleEdge], int]:
    """``kept`` without the imports that an inheritance arrow on screen stands for.

    An import of a pair is left out when the same pair has a kept ``==>``,
    which then stands for the import too. It stays when that inheritance edge
    was itself reduced away (nothing would be drawn on the pair), and when it
    closes an import cycle (the cycle must stay readable as imports). Applied
    after the reduction, so which imports are implied by a longer path is
    decided over all of them.
    """
    extends = {(e.source, e.target) for e in kept if e.kind == "extends"}
    imports = [e for e in kept if e.kind == "imports"]
    comp, _ = _condense(imports)
    shown = [
        e
        for e in kept
        if e.kind != "imports"
        or (e.source, e.target) not in extends
        or comp[e.source] == comp[e.target]
    ]
    return shown, len(kept) - len(shown)


def _reduced(
    found: Iterable[ModuleEdge], layers: list[str], reduce: bool
) -> tuple[list[ModuleEdge], dict[str, int]]:
    """Each layer reduced on its own, and how many edges each lost."""
    all_edges = list(found)
    kept: list[ModuleEdge] = []
    dropped: dict[str, int] = {}
    for layer in layers:
        layer_edges = [e for e in all_edges if e.kind == layer]
        reduced = transitive_reduction(layer_edges) if reduce else layer_edges
        dropped[layer] = len(layer_edges) - len(reduced)
        kept.extend(reduced)
    return sorted(kept, key=lambda e: (e.kind, e.source, e.target)), dropped


def _collect(
    edges: Sequence[Edge], scope: _Scope, layers: list[str]
) -> tuple[list[ModuleEdge], dict[str, set[str]]]:
    """The member-to-member edges of ``layers``, and per member the packages outside it reaches."""
    wanted = {edge_type: layer for layer in layers for edge_type in KINDS[layer]}
    found: dict[ModuleEdge, None] = {}
    outside: dict[str, set[str]] = {}
    for e in edges:
        kind = wanted.get(e.type)
        ends = None if kind is None or e.type_only else _ends(e, scope)
        if kind is None or ends is None:
            continue
        src, dst_file = ends
        if not _under(dst_file, scope.prefix):
            outside.setdefault(src, set()).add(_outside_package(dst_file, scope.packages))
            continue
        dst = _group(dst_file, scope.prefix, scope.expand)
        if dst in scope.members and dst != src:
            found[ModuleEdge(src, dst, kind)] = None
    return list(found), outside


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
    chosen = checked_layers(layers)
    holder, tests, packages = _file_index(nodes)
    skipped = tests if exclude_tests else set()
    inside = sorted(
        f for f in set(holder.values()) if _under(f, prefix) and f != prefix and f not in skipped
    )
    members = sorted({_group(f, prefix, expand) for f in inside})
    scope = _Scope(
        prefix=prefix,
        expand=expand,
        holder=holder,
        skipped=skipped,
        packages=packages,
        inside=set(inside),
        members=set(members),
        through=_imported_through(edges, holder) if expand else {},
    )
    found, outside = _collect(edges, scope, chosen)
    kept, dropped = _reduced(found, chosen, reduce)
    kept, covered = _uncovered(kept)
    return ModuleGraph(
        prefix=prefix,
        nodes=members,
        subpackages=[]
        if expand
        else [m for m in members if any(f.startswith(m + ".") for f in inside)],
        edges=kept,
        dropped=dropped,
        outside={m: sorted(pkgs) for m, pkgs in sorted(outside.items())},
        covered=covered,
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


def _node_ids(names: Iterable[str], prefix: str) -> dict[str, str]:
    """A Mermaid id per name, ``m_`` + the name folded to ``[A-Za-z0-9_]``, made unique.

    Folding is not injective (``a.b`` and ``a_b`` both give ``a_b``); a later
    name that collides gets ``_2``, ``_3``…, in sorted order, so ids are stable.
    """
    ids: dict[str, str] = {}
    used: set[str] = set()
    for name in sorted(set(names)):
        base = "m_" + (_NON_ID.sub("_", _short(name, prefix)).strip("_") or "root")
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}_{n}", n + 1
        used.add(slug)
        ids[name] = slug
    return ids


def render_mermaid(graph: ModuleGraph) -> str:
    """The graph as a Mermaid flowchart, with outbound dependencies as a table under it.

    Imports are ``-->``, inheritance ``==>`` and calls ``-.->``. Node ids carry an
    ``m_`` prefix, so a module named ``end`` or ``graph`` is not a keyword.
    """
    lines = ["```mermaid", "flowchart TD"]
    ids = _node_ids(
        [*graph.nodes, *(e.source for e in graph.edges), *(e.target for e in graph.edges)],
        graph.prefix,
    )
    for name in graph.nodes:
        label = _short(name, graph.prefix)
        shape = f'[["{label}/"]]' if name in graph.subpackages else f'["{label}"]'
        lines.append(f"    {ids[name]}{shape}")
    lines.extend(f"    {ids[e.source]} {_ARROWS[e.kind]} {ids[e.target]}" for e in graph.edges)
    lines.append("```")
    kinds = ", ".join(f"{k} {_ARROWS[k]}" for k in graph.dropped)
    removed = ", ".join(f"{k} {n}" for k, n in graph.dropped.items() if n)
    lines.append("")
    lines.append(
        f"Edges: {kinds}."
        + (f" Implied edges dropped: {removed}." if removed else "")
        + (f" Imports drawn as the ==> of the same pair: {graph.covered}." if graph.covered else "")
    )
    if graph.outside:
        lines += ["", f"| module | reaches outside `{graph.prefix}` |", "|---|---|"]
        for module, packages in graph.outside.items():
            lines.append(f"| {_short(module, graph.prefix)} | {', '.join(packages)} |")
    return "\n".join(lines)


def graph_to_dict(graph: ModuleGraph) -> dict[str, object]:
    """The graph as plain data.

    ``{prefix, nodes, subpackages, edges, dropped, covered, outside}``.
    """
    return {
        "prefix": graph.prefix,
        "nodes": graph.nodes,
        "subpackages": graph.subpackages,
        "edges": [{"source": e.source, "target": e.target, "kind": e.kind} for e in graph.edges],
        "dropped": graph.dropped,
        "covered": graph.covered,
        "outside": graph.outside,
    }
