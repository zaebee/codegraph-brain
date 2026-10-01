"""Domain-quotient graph: collapse each domain to one node (spec §3.4).

The quotient is scored by the SAME FingerprintExtractor + DriftScorer as
module-level domains — that closure under coarsening is the point, not an
implementation convenience.
"""

from collections import Counter

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift.drift import DomainConfig
from cgis.query.drift.fingerprint import CallTally
from cgis.query.engine import is_unresolved

#: FQN prefix of quotient nodes; the project_level binding matches it.
QUOTIENT_PREFIX = "quotient"

_QUOTIENT_EDGE_TYPES = frozenset({EdgeType.IMPORTS, EdgeType.CALLS})


def build_quotient(
    nodes: list[Node], edges: list[Edge], domains: list[DomainConfig]
) -> tuple[list[Node], list[Edge]]:
    """Return (quotient_nodes, quotient_edges) for the given domain bindings.

    One MODULE node per domain (id = quotient.<name>); cross-domain IMPORTS
    and CALLS edges aggregate per (source domain, target domain, type) with
    weight = aggregated edge count. Intra-domain edges and edges touching
    nodes outside every domain are dropped — unresolved call targets among
    them, so the unresolved share the CALLS layer is discounted by is
    carried over separately: see `quotient_call_tally`.
    """
    domain_of = _domain_of(nodes, domains)

    qnodes = [
        Node(
            id=f"{QUOTIENT_PREFIX}.{d.name}",
            type=NodeType.MODULE,
            name=d.name,
            file_path=d.fqn_prefix,
            start_line=0,
            end_line=0,
        )
        for d in domains
    ]

    counts: Counter[tuple[str, str, EdgeType]] = Counter(
        (domain_of[e.source], domain_of[e.target], e.type)
        for e in edges
        if e.type in _QUOTIENT_EDGE_TYPES
        and e.source in domain_of
        and e.target in domain_of
        and domain_of[e.source] != domain_of[e.target]
    )

    qedges = [
        Edge(
            id=f"{QUOTIENT_PREFIX}.{src}:{etype.value}:{QUOTIENT_PREFIX}.{dst}",
            source=f"{QUOTIENT_PREFIX}.{src}",
            target=f"{QUOTIENT_PREFIX}.{dst}",
            type=etype,
            weight=float(count),
            confidence=1.0,
        )
        for (src, dst, etype), count in sorted(
            counts.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2].value)
        )
    ]
    return qnodes, qedges


def quotient_call_tally(
    nodes: list[Node], edges: list[Edge], domains: list[DomainConfig]
) -> CallTally:
    """Quotient node id -> (unresolved calls, all calls) out of its domain's members (#149).

    What `FingerprintExtractor.from_graph` needs to give the quotient the same
    unresolved_ratio its members have: summed over a prefix, it is the
    call-weighted mean of the member domains' ratios, the quantity each of
    them is discounted by at k=0. Without it the ratio read 0 by construction —
    `build_quotient` keeps no unresolved edge — and the k=1 CALLS layer went
    undiscounted however poorly the code under it resolved.
    """
    domain_of = _domain_of(nodes, domains)
    known = {n.id: n for n in nodes}
    unresolved: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for e in edges:
        if e.type is not EdgeType.CALLS or e.source not in domain_of:
            continue
        qid = f"{QUOTIENT_PREFIX}.{domain_of[e.source]}"
        total[qid] += 1
        if is_unresolved(e.target, known):
            unresolved[qid] += 1
    return {qid: (unresolved[qid], count) for qid, count in total.items()}


def _domain_of(nodes: list[Node], domains: list[DomainConfig]) -> dict[str, str]:
    """Node id -> the name of the domain it belongs to, for nodes inside any domain.

    Longest-prefix match: if one domain's prefix nests inside another's, the
    most specific binding wins regardless of declaration order.
    """
    by_specificity = sorted(domains, key=lambda d: len(d.fqn_prefix), reverse=True)
    domain_of: dict[str, str] = {}
    for n in nodes:
        for d in by_specificity:
            if n.id == d.fqn_prefix or n.id.startswith(d.fqn_prefix + "."):
                domain_of[n.id] = d.name
                break
    return domain_of
