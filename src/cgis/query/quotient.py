"""Domain-quotient graph: collapse each domain to one node (spec §3.4).

The quotient is scored by the SAME FingerprintExtractor + DriftScorer as
module-level domains — that closure under coarsening is the point, not an
implementation convenience.
"""

from collections import Counter

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift import DomainConfig

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
    nodes outside every domain are dropped (raw_call targets land here, so
    the quotient's unresolved_ratio is 0 for the observe-only milestone —
    recorded in tests/self_parsing/test_drift.py).
    """
    # Longest-prefix match: if one domain's prefix nests inside another's,
    # the most specific binding wins regardless of declaration order.
    by_specificity = sorted(domains, key=lambda d: len(d.fqn_prefix), reverse=True)
    domain_of: dict[str, str] = {}
    for n in nodes:
        for d in by_specificity:
            if n.id == d.fqn_prefix or n.id.startswith(d.fqn_prefix + "."):
                domain_of[n.id] = d.name
                break

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
