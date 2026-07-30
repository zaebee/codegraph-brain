"""Structural tier ladder + entropy slope — the #186 deliverable-3 measurement.

Reproduces the baseline table in docs/specs/2026-07-30-cgis-fractal-design.md.
Rungs are structural and their count is set by the repository, never by a swept
parameter: symbol -> class -> module -> directory levels trimmed from the LEAF end.

Usage:
    uv run cgis ingest src --source-root src -o /tmp/cgis.db
    uv run python scripts/probe_tier_ladder.py cgis=/tmp/cgis.db [more=other.db ...]
"""

import argparse
import math
from collections.abc import Iterable

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift.triads import TRIAD_ORDER, normalized_census, tangle_mass, triad_census
from cgis.storage.sqlite_store import SQLiteStore

LAYERS: tuple[EdgeType, ...] = (EdgeType.IMPORTS, EdgeType.CALLS)

#: Structural containment edges — the only ones a parent-walk may follow.
_STRUCT = frozenset({EdgeType.CONTAINS, EdgeType.DECLARES})
_SYMBOL = frozenset({NodeType.FUNCTION, NodeType.METHOD, NodeType.VARIABLE})
_FILE_LIKE = frozenset({NodeType.FILE, NodeType.MODULE})

#: Data-sufficiency floor: a rung below this many triads is reported but not
#: fitted. It decides whether a rung is OBSERVED, never what the verdict is.
MIN_RUNG_TRIADS = 10
#: Fewer live rungs than this and there is no curve to fit.
MIN_LIVE_RUNGS = 3

Grouping = dict[str, str]


def parent_map(nodes: list[Node], edges: list[Edge]) -> dict[str, str]:
    """Child id -> parent id, from CONTAINS / DECLARES."""
    ids = {n.id for n in nodes}
    return {e.target: e.source for e in edges if e.type in _STRUCT and e.source in ids}


def walk_to(
    nid: str, parents: dict[str, str], types: dict[str, NodeType], stop: frozenset[NodeType]
) -> str:
    """Walk up the containment chain to the nearest ancestor of a stop type.

    Cycle-guarded; a node with no such ancestor is its own group.
    """
    seen: set[str] = set()
    cur: str | None = nid
    while cur is not None and cur not in seen:
        if types.get(cur) in stop:
            return cur
        seen.add(cur)
        cur = parents.get(cur)
    return nid


def build_ladder(nodes: list[Node], edges: list[Edge]) -> list[tuple[str, Grouping]]:
    """Return [(rung name, node id -> group id)] from the graph's own structure."""
    parents = parent_map(nodes, edges)
    types = {n.id: n.type for n in nodes}
    by_id = {n.id: n for n in nodes}

    file_of = {n.id: walk_to(n.id, parents, types, _FILE_LIKE) for n in nodes}
    dirs: dict[str, list[str]] = {}
    for nid, fid in file_of.items():
        node = by_id.get(fid)
        dirs[nid] = node.file_path.split("/")[:-1] if node and node.file_path else []

    class_of = {
        n.id: (
            walk_to(n.id, parents, types, _FILE_LIKE | {NodeType.CLASS})
            if n.type in _SYMBOL
            else n.id
        )
        for n in nodes
    }
    rungs: list[tuple[str, Grouping]] = [
        ("T0_symbol", {n.id: n.id for n in nodes}),
        ("T1_class", class_of),
        ("T2_module", file_of),
    ]
    # Directory rungs trim from the LEAF end, so every file moves at every rung.
    # Trimming from the root end leaves shallow files stationary and produces
    # near-duplicate rungs — the FQN-truncation artifact in path coordinates.
    for k in range(1, max((len(d) for d in dirs.values()), default=0) + 1):
        rungs.append(
            (
                f"T{len(rungs)}_up{k}",
                {nid: "/".join(d[: max(len(d) - k + 1, 0)]) or "<root>" for nid, d in dirs.items()},
            )
        )
    return rungs


def quotient_edges(group_of: Grouping, edges: list[Edge], layer: EdgeType) -> list[Edge]:
    """Distinct cross-group edges of one layer; self-loops dropped."""
    pairs = {
        (group_of[e.source], group_of[e.target])
        for e in edges
        if e.type is layer
        and e.source in group_of
        and e.target in group_of
        and group_of[e.source] != group_of[e.target]
    }
    return [
        Edge(
            id=f"{u}:{layer.value}:{v}", source=u, target=v, type=layer, weight=1.0, confidence=1.0
        )
        for u, v in sorted(pairs)
    ]


def entropy_bits(vec: Iterable[float]) -> float:
    """Shannon entropy of a normalized census, in bits (max log2(13) ~ 3.70)."""
    return -sum(p * math.log2(p) for p in vec if p > 0)


class RungRow:
    """One rung's measurement on one layer."""

    def __init__(self, name: str, groups: int, triads: int, census: tuple[float, ...]) -> None:
        """Store the raw counts and derive entropy, dominant motif and tangle."""
        self.name = name
        self.groups = groups
        self.triads = triads
        self.census = census
        self.live = triads >= MIN_RUNG_TRIADS and groups > 1
        self.entropy = entropy_bits(census) if triads else None
        self.dominant = (
            max(zip(TRIAD_ORDER, census, strict=True), key=lambda kv: kv[1])
            if triads
            else ("-", 0.0)
        )
        self.tangle = tangle_mass(census)


def measure(nodes: list[Node], edges: list[Edge], layer: EdgeType) -> list[RungRow]:
    """Measure every rung on one layer, deduplicating identical censuses.

    IMPORTS edges connect only FILE nodes, so T0/T1/T2 are literally the same
    import quotient — counting them three times would triple-weight one
    observation in the fit.
    """
    rows: list[RungRow] = []
    for name, grouping in build_ladder(nodes, edges):
        gids = set(grouping.values())
        census = triad_census(gids, quotient_edges(grouping, edges, layer), layer)
        vec = normalized_census(census)
        if rows and rows[-1].census == vec and rows[-1].triads == sum(census.values()):
            continue
        rows.append(RungRow(name, len(gids), sum(census.values()), vec))
    return rows


def fit_slope(rows: list[RungRow]) -> tuple[float, float, float] | None:
    """Least-squares (slope, r_squared, standard_error) of H against -log2(groups).

    x increases as the graph coarsens, so a positive slope means coarsening adds
    motif diversity. Returns None when there is no curve to fit.
    """
    pts = [(-math.log2(r.groups), r.entropy) for r in rows if r.live and r.entropy is not None]
    if len(pts) < MIN_LIVE_RUNGS:
        return None
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-12:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys, strict=True))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else float("nan")
    se = math.sqrt(ss_res / (len(pts) - 2) / sxx) if len(pts) > 2 else float("inf")
    return slope, r2, se


def verdict(fit: tuple[float, float, float] | None) -> str:
    """Three-way verdict from the sign of the slope with a 2*SE dead-band."""
    if fit is None:
        return "no_signal"
    slope, _, se = fit
    band = 2.0 * se
    if not math.isfinite(band):
        return "no_signal"
    if slope > band:
        return "hierarchical"
    if slope < -band:
        return "flat"
    return "scale_invariant"


def report(label: str, nodes: list[Node], edges: list[Edge]) -> None:
    """Print the ladder, the fit and the verdict for every layer."""
    print(f"\n=== {label}: {len(nodes)} nodes")
    for layer in LAYERS:
        rows = measure(nodes, edges, layer)
        fit = fit_slope(rows)
        live = sum(1 for r in rows if r.live)
        head = f"  -- {layer.value}: rungs={len(rows)} live={live} verdict={verdict(fit)}"
        if fit is not None:
            slope, r2, se = fit
            head += f" slope={slope:+.3f} R2={r2:.2f} SE={se:.3f} band=+-{2 * se:.3f}"
        print(head)
        for r in rows:
            h = "  n/a" if r.entropy is None else f"{r.entropy:.2f}"
            flag = "" if r.live else "  (no_signal)"
            print(
                f"     {r.name:<12} groups={r.groups:<6} triads={r.triads:<8} H={h}"
                f"  dom={r.dominant[0]}:{r.dominant[1]:.2f} tangle={r.tangle:.3f}{flag}"
            )


def main() -> None:
    """Measure every ``label=path.db`` given on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graphs", nargs="+", metavar="LABEL=DB", help="graph DBs to measure")
    args = parser.parse_args()
    for spec in args.graphs:
        label, _, path = spec.partition("=")
        with SQLiteStore(path or label) as store:
            report(label, store.get_all_nodes(), store.get_all_edges())


if __name__ == "__main__":
    main()
