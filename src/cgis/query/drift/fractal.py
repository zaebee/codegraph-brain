"""Structural tier ladder and its entropy slope (spec 2026-07-30, #186).

Coarsens the graph along its OWN structure — symbol, class, module, then
directory levels — and measures the 13-triad census at every rung. The number of
rungs is set by the repository, never by a swept parameter: grain-dependence is
what retired the closure-gap metric on the same issue.
"""

import math
from dataclasses import dataclass

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift.triads import TRIAD_ORDER, normalized_census, tangle_mass, triad_census

#: Layers the ladder is measured on, in report order.
LADDER_LAYERS: tuple[EdgeType, ...] = (EdgeType.IMPORTS, EdgeType.CALLS)

#: The only structural edge types in the graph; a parent-walk follows these.
_STRUCT_EDGES = frozenset({EdgeType.CONTAINS, EdgeType.DECLARES})
_SYMBOL_TYPES = frozenset({NodeType.FUNCTION, NodeType.METHOD, NodeType.VARIABLE})
_FILE_TYPES = frozenset({NodeType.FILE, NodeType.MODULE})

#: Data-sufficiency floor: a rung below this many triads is reported but not
#: fitted. It decides whether a rung is OBSERVED, never what the verdict is.
MIN_RUNG_TRIADS = 10

#: Fewer live rungs than this and there is no curve to fit.
MIN_LIVE_RUNGS = 3

#: Group id for a file that has been folded above its top directory.
ROOT_GROUP = "<root>"

Grouping = dict[str, str]


def _parent_map(nodes: list[Node], edges: list[Edge]) -> dict[str, str]:
    """Child id -> parent id, from CONTAINS / DECLARES edges."""
    ids = {n.id for n in nodes}
    return {e.target: e.source for e in edges if e.type in _STRUCT_EDGES and e.source in ids}


def _walk_to(
    node_id: str,
    parents: dict[str, str],
    types: dict[str, NodeType],
    stop: frozenset[NodeType],
) -> str:
    """Walk up the containment chain to the nearest ancestor of a stop type.

    Cycle-guarded: a malformed graph returns the node itself rather than
    looping. A node with no ancestor of a stop type is its own group.
    """
    seen: set[str] = set()
    current: str | None = node_id
    while current is not None and current not in seen:
        if types.get(current) in stop:
            return current
        seen.add(current)
        current = parents.get(current)
    return node_id


def _directory_parts(nodes: list[Node], file_of: Grouping) -> dict[str, list[str]]:
    """Node id -> the directory components of its file's path."""
    by_id = {n.id: n for n in nodes}
    parts: dict[str, list[str]] = {}
    for node_id, file_id in file_of.items():
        node = by_id.get(file_id)
        parts[node_id] = node.file_path.split("/")[:-1] if node and node.file_path else []
    return parts


def build_ladder(nodes: list[Node], edges: list[Edge]) -> list[tuple[str, Grouping]]:
    """Return the repository's structural rungs, finest first.

    ``T0_symbol`` is the identity grouping, ``T1_class`` folds symbols into their
    declaring class, ``T2_module`` folds everything into its file, and each
    ``Tn_upk`` folds files into a directory with ``k - 1`` components trimmed
    **from the leaf end** — so every file moves at every rung until it bottoms
    out at ``<root>``.
    """
    parents = _parent_map(nodes, edges)
    types = {n.id: n.type for n in nodes}

    file_of = {n.id: _walk_to(n.id, parents, types, _FILE_TYPES) for n in nodes}
    class_of = {
        n.id: (
            _walk_to(n.id, parents, types, _FILE_TYPES | {NodeType.CLASS})
            if n.type in _SYMBOL_TYPES
            else n.id
        )
        for n in nodes
    }
    parts = _directory_parts(nodes, file_of)

    rungs: list[tuple[str, Grouping]] = [
        ("T0_symbol", {n.id: n.id for n in nodes}),
        ("T1_class", class_of),
        ("T2_module", file_of),
    ]
    depth = max((len(p) for p in parts.values()), default=0)
    for k in range(1, depth + 1):
        rungs.append(
            (
                f"T{len(rungs)}_up{k}",
                {
                    node_id: "/".join(p[: max(len(p) - k + 1, 0)]) or ROOT_GROUP
                    for node_id, p in parts.items()
                },
            )
        )
    return rungs


@dataclass(frozen=True)
class RungReport:
    """One rung of the ladder measured on one layer.

    ``live`` marks a rung with enough triads to enter the fit; thin and
    single-group rungs are still reported so the curve stays readable.
    """

    name: str
    groups: int
    triads: int
    census: tuple[float, ...]
    entropy: float | None
    dominant: str
    dominant_share: float
    tangle_ratio: float
    live: bool


def entropy_bits(census: tuple[float, ...]) -> float:
    """Shannon entropy of a normalized census, in bits (max log2(13) ~ 3.70)."""
    return -sum(p * math.log2(p) for p in census if p > 0)


def _quotient_edges(grouping: Grouping, edges: list[Edge], layer: EdgeType) -> list[Edge]:
    """Distinct cross-group edges of one layer; self-loops dropped."""
    pairs = {
        (grouping[e.source], grouping[e.target])
        for e in edges
        if e.type is layer
        and e.source in grouping
        and e.target in grouping
        and grouping[e.source] != grouping[e.target]
    }
    return [
        Edge(
            id=f"{u}:{layer.value}:{v}",
            source=u,
            target=v,
            type=layer,
            weight=1.0,
            confidence=1.0,
        )
        for u, v in sorted(pairs)
    ]


def _rung_report(name: str, groups: int, counts: dict[str, int]) -> RungReport:
    """Build one RungReport from a raw triad census."""
    triads = sum(counts.values())
    census = normalized_census(counts)
    dominant, share = (
        max(zip(TRIAD_ORDER, census, strict=True), key=lambda kv: kv[1]) if triads else ("-", 0.0)
    )
    return RungReport(
        name=name,
        groups=groups,
        triads=triads,
        census=census,
        entropy=entropy_bits(census) if triads else None,
        dominant=dominant,
        dominant_share=share,
        tangle_ratio=tangle_mass(census),
        live=triads >= MIN_RUNG_TRIADS and groups > 1,
    )


def measure_layer(nodes: list[Node], edges: list[Edge], layer: EdgeType) -> list[RungReport]:
    """Measure every rung on one layer, collapsing rungs with identical censuses.

    The dedup is not an optimization. IMPORTS edges connect only FILE nodes, so
    ``T0``/``T1``/``T2`` are literally the same import quotient — counting them
    three times would triple-weight one observation in the fit.
    """
    rows: list[RungReport] = []
    for name, grouping in build_ladder(nodes, edges):
        groups = set(grouping.values())
        counts = triad_census(groups, _quotient_edges(grouping, edges, layer), layer)
        row = _rung_report(name, len(groups), counts)
        if rows and rows[-1].census == row.census and rows[-1].triads == row.triads:
            continue
        rows.append(row)
    return rows
