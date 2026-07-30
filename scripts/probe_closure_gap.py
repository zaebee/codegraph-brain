"""Closure-gap probe — the measurement that RETIRED #186 deliverable 2.

Kept as reproducible evidence, not as a supported metric. Every definition below
fails; see the negative-result comment on issue #186. The short version: the
verdict is a function of the chosen domain grain, so a coupling score cannot be
built on the quotient until a canonical grain exists.

Subcommands:
    segment   place the macro census on [micro <-> closure(micro)], 3 definitions
    null      edge-level saturation vs a degree-preserving rewiring null model
    grain     the kill shot: sweep FQN depth and watch the ranking reshuffle

Usage:
    uv run python scripts/probe_closure_gap.py segment cgis=/tmp/cgis.db
    uv run python scripts/probe_closure_gap.py grain cgis=/tmp/cgis.db django=/tmp/dj.db
"""

import argparse
import math
import random
import statistics

from cgis.core.models import Edge, EdgeType, Node
from cgis.query.drift.drift import DomainConfig
from cgis.query.drift.ontology_init import discover_domains
from cgis.query.drift.quotient import QUOTIENT_PREFIX, build_quotient
from cgis.query.drift.triads import TRIAD_ORDER, normalized_census, triad_census
from cgis.storage.sqlite_store import SQLiteStore

LAYERS: tuple[EdgeType, ...] = (EdgeType.IMPORTS, EdgeType.CALLS)
_IX = {name: i for i, name in enumerate(TRIAD_ORDER)}

#: Definition B's closure map on triad classes: closing a chain materializes the
#: composed edge; motifs with no composable path are fixed points.
C_MAP = {"021C": "030T", "030C": "300", "111D": "120D", "111U": "120U", "201": "210"}

NULL_SAMPLES = 300
Arc = tuple[str, str]


def domains_of(nodes: list[Node], depth: int | None = None) -> list[DomainConfig]:
    """Auto-discovered domain bindings at the given FQN depth (None = auto-descent)."""
    return [
        DomainConfig(name=p.replace(".", "_"), fqn_prefix=p, expected_pattern=None)
        for p in discover_domains(nodes, depth=depth)
    ]


def successors(node_ids: set[str], edges: list[Edge], layer: EdgeType) -> dict[str, set[str]]:
    """Successor map over intra-set edges of one layer; self-loops dropped."""
    succ: dict[str, set[str]] = {n: set() for n in node_ids}
    for e in edges:
        if e.type is layer and e.source in succ and e.target in succ and e.source != e.target:
            succ[e.source].add(e.target)
    return succ


def reachable(succ: dict[str, set[str]], sources: list[str]) -> set[str]:
    """Multi-source forward reachability."""
    seen, stack = set(sources), list(sources)
    while stack:
        for v in succ.get(stack.pop(), ()):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def as_edges(pairs: set[Arc], layer: EdgeType) -> list[Edge]:
    """Materialize (source, target) pairs as Edge objects of one layer."""
    return [
        Edge(
            id=f"{u}:{layer.value}:{v}", source=u, target=v, type=layer, weight=1.0, confidence=1.0
        )
        for u, v in sorted(pairs)
    ]


def closure_of_quotient(qids: set[str], qedges: list[Edge], layer: EdgeType) -> list[Edge]:
    """Definition A: transitive closure computed on the quotient graph itself."""
    succ = successors(qids, qedges, layer)
    return as_edges({(s, v) for s in qids for v in reachable(succ, [s]) - {s}}, layer)


def quotient_of_closure(
    nodes: list[Node], edges: list[Edge], domains: list[DomainConfig], layer: EdgeType
) -> list[Edge]:
    """Definition A': domain-to-domain reachability over the FULL graph, then quotient."""
    succ = successors({n.id for n in nodes}, edges, layer)
    domain_of = _domain_of(nodes, domains)
    pairs: set[Arc] = set()
    for d in domains:
        sources = [nid for nid, name in domain_of.items() if name == d.name]
        for nid in reachable(succ, sources):
            other = domain_of.get(nid)
            if other is not None and other != d.name:
                pairs.add((f"{QUOTIENT_PREFIX}.{d.name}", f"{QUOTIENT_PREFIX}.{other}"))
    return as_edges(pairs, layer)


def _domain_of(nodes: list[Node], domains: list[DomainConfig]) -> dict[str, str]:
    """Node id -> domain name, longest prefix wins (mirrors build_quotient)."""
    by_specificity = sorted(domains, key=lambda d: len(d.fqn_prefix), reverse=True)
    out: dict[str, str] = {}
    for n in nodes:
        for d in by_specificity:
            if n.id == d.fqn_prefix or n.id.startswith(d.fqn_prefix + "."):
                out[n.id] = d.name
                break
    return out


def algebraic_closure(vec: tuple[float, ...]) -> tuple[float, ...]:
    """Definition B: move each triad's mass to its closure class."""
    out = [0.0] * len(TRIAD_ORDER)
    for name, i in _IX.items():
        out[_IX[C_MAP.get(name, name)]] += vec[i]
    return tuple(out)


def micro_census(
    nodes: list[Node], edges: list[Edge], domains: list[DomainConfig], layer: EdgeType
) -> tuple[tuple[float, ...], int]:
    """Census of the disjoint union of every domain's intra-domain subgraph."""
    total = dict.fromkeys(TRIAD_ORDER, 0)
    for d in domains:
        ids = {n.id for n in nodes if n.id == d.fqn_prefix or n.id.startswith(d.fqn_prefix + ".")}
        if len(ids) < 3:
            continue
        internal = [e for e in edges if e.source in ids and e.target in ids]
        for name, count in triad_census(ids, internal, layer).items():
            total[name] += count
    return normalized_census(total), sum(total.values())


def project(
    a: tuple[float, ...], m: tuple[float, ...], c: tuple[float, ...]
) -> tuple[float | None, float | None, float]:
    """Scalar projection of a onto the segment [m, c], plus the off-line residual."""
    d = [ci - mi for ci, mi in zip(c, m, strict=True)]
    dd = sum(x * x for x in d)
    if dd < 1e-9:
        return None, None, 0.0
    am = [ai - mi for ai, mi in zip(a, m, strict=True)]
    t = sum(x * y for x, y in zip(am, d, strict=True)) / dd
    resid = math.sqrt(sum((x - t * y) ** 2 for x, y in zip(am, d, strict=True)))
    return t, resid, math.sqrt(dd)


def saturation(nodes: set[str], arcs: list[Arc]) -> float | None:
    """|E| / |E(transitive closure)| — how closed the graph already is."""
    if not arcs:
        return None
    succ: dict[str, set[str]] = {n: set() for n in nodes}
    for u, v in arcs:
        succ[u].add(v)
    total = sum(len(reachable(succ, [s]) - {s}) for s in nodes)
    return len(arcs) / total if total else None


def rewire(arcs: list[Arc], rng: random.Random, rounds: int = 20) -> list[Arc]:
    """Degree-preserving double-edge swaps: (a,b),(c,d) -> (a,d),(c,b)."""
    arcs = list(arcs)
    present = set(arcs)
    for _ in range(rounds * len(arcs)):
        i, j = rng.randrange(len(arcs)), rng.randrange(len(arcs))
        (a, b), (c, d) = arcs[i], arcs[j]
        if len({a, b, c, d}) < 4 or (a, d) in present or (c, b) in present:
            continue
        present.difference_update({(a, b), (c, d)})
        present.update({(a, d), (c, b)})
        arcs[i], arcs[j] = (a, d), (c, b)
    return arcs


def cmd_segment(label: str, nodes: list[Node], edges: list[Edge], _rng: random.Random) -> None:
    """Report definitions A / A' / B against the macro census."""
    domains = domains_of(nodes)
    qnodes, qedges = build_quotient(nodes, edges, domains)
    qids = {n.id for n in qnodes}
    print(f"\n=== {label}: {len(nodes)} nodes, {len(domains)} domains")
    for layer in LAYERS:
        counts = triad_census(qids, qedges, layer)
        macro, n_triads = normalized_census(counts), sum(counts.values())
        micro, m_triads = micro_census(nodes, edges, domains, layer)
        if not n_triads or not m_triads:
            print(f"  {layer.value:<8} no_signal (macro={n_triads}, micro={m_triads})")
            continue
        ends = {
            "A ": normalized_census(
                triad_census(qids, closure_of_quotient(qids, qedges, layer), layer)
            ),
            "A'": normalized_census(
                triad_census(qids, quotient_of_closure(nodes, edges, domains, layer), layer)
            ),
            "B ": algebraic_closure(micro),
        }
        cells = []
        for name, end in ends.items():
            gap, resid, seplen = project(macro, micro, end)
            gap_s = "n/a" if gap is None else f"{gap:+.2f}"
            res_s = "n/a" if resid is None else f"{resid:.2f}"
            cells.append(f"{name} gap={gap_s} res={res_s} |c-m|={seplen:.2f}")
        print(f"  {layer.value:<8} triads={n_triads:<6} | " + " | ".join(cells))


def cmd_null(label: str, nodes: list[Node], edges: list[Edge], rng: random.Random) -> None:
    """Compare edge-level saturation against a degree-preserving null model."""
    qnodes, qedges = build_quotient(nodes, edges, domains_of(nodes))
    qids = {n.id for n in qnodes}
    print(f"\n=== {label}: {len(qids)} domains")
    for layer in LAYERS:
        arcs = [(e.source, e.target) for e in qedges if e.type is layer and e.source != e.target]
        obs = saturation(qids, arcs)
        if obs is None or len(arcs) < 4:
            print(f"  {layer.value:<8} no_signal (E={len(arcs)})")
            continue
        null = [s for s in (saturation(qids, rewire(arcs, rng)) for _ in range(NULL_SAMPLES)) if s]
        mu, sd = statistics.fmean(null), statistics.pstdev(null)
        z = (obs - mu) / sd if sd > 1e-9 else float("nan")
        print(
            f"  {layer.value:<8} E={len(arcs):<4} obs={obs:.3f} null={mu:.3f}+-{sd:.3f} z={z:+.2f}"
        )


def cmd_grain(label: str, nodes: list[Node], edges: list[Edge], rng: random.Random) -> None:
    """Sweep FQN depth — the ranking reshuffles, which is why the metric was retired."""
    print(f"\n=== {label}")
    for depth in (2, 3, 4, 5):
        domains = domains_of(nodes, depth=depth)
        if len(domains) < 6:
            continue
        qnodes, qedges = build_quotient(nodes, edges, domains)
        qids = {n.id for n in qnodes}
        arcs = [
            (e.source, e.target)
            for e in qedges
            if e.type is EdgeType.CALLS and e.source != e.target
        ]
        if len(arcs) < 6:
            print(f"  depth={depth} n={len(qids):<5} E={len(arcs):<6} no_signal")
            continue
        obs = saturation(qids, arcs)
        null = [s for s in (saturation(qids, rewire(arcs, rng, rounds=8)) for _ in range(120)) if s]
        mu, sd = statistics.fmean(null), statistics.pstdev(null)
        z = (obs - mu) / sd if obs is not None and sd > 1e-6 else float("nan")
        print(f"  depth={depth} n={len(qids):<5} E={len(arcs):<6} obs={obs:.3f} z={z:+.2f}")


_COMMANDS = {"segment": cmd_segment, "null": cmd_null, "grain": cmd_grain}


def main() -> None:
    """Run one subcommand over every ``label=path.db`` given on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(_COMMANDS))
    parser.add_argument("graphs", nargs="+", metavar="LABEL=DB")
    parser.add_argument("--seed", type=int, default=20260729)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    run = _COMMANDS[args.command]
    for spec in args.graphs:
        label, _, path = spec.partition("=")
        with SQLiteStore(path or label) as store:
            run(label, store.get_all_nodes(), store.get_all_edges(), rng)


if __name__ == "__main__":
    main()
