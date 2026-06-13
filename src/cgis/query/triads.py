"""13-class connected triad census and total-variation distance (spec ss3.1-3.3).

Classification uses the Batagelj-Mrvar tricode: the 6 possible directed edges
among an ordered triple form a 6-bit code; a 64-entry table maps each code to
one of the 16 MAN triad types. We count only the 13 *connected* types -- the
unit tests pin one hand-built graph per class, which is the correctness anchor
for the table.
"""

from collections.abc import Iterator

from cgis.core.models import Edge, EdgeType

#: The 13 connected triad classes, canonical vector order for fingerprints.
TRIAD_ORDER: tuple[str, ...] = (
    "021D",
    "021U",
    "021C",
    "111D",
    "111U",
    "030T",
    "030C",
    "201",
    "120D",
    "120U",
    "120C",
    "210",
    "300",
)

#: All 16 MAN types in Batagelj-Mrvar order; the first three are disconnected.
_TRIAD_NAMES: tuple[str, ...] = (
    "003",
    "012",
    "102",
    "021D",
    "021U",
    "021C",
    "111D",
    "111U",
    "030T",
    "030C",
    "201",
    "120D",
    "120U",
    "120C",
    "210",
    "300",
)

#: Batagelj-Mrvar TRICODES: maps each 6-bit edge code to a 1-based MAN index.
_TRICODES: tuple[int, ...] = (
    1,
    2,
    2,
    3,
    2,
    4,
    6,
    8,
    2,
    6,
    5,
    7,
    3,
    8,
    7,
    11,
    2,
    6,
    4,
    8,
    5,
    9,
    9,
    13,
    6,
    10,
    9,
    14,
    7,
    14,
    12,
    15,
    2,
    5,
    6,
    7,
    6,
    9,
    10,
    14,
    4,
    9,
    9,
    12,
    8,
    13,
    14,
    15,
    3,
    7,
    8,
    11,
    7,
    12,
    14,
    15,
    8,
    14,
    13,
    15,
    11,
    15,
    15,
    16,
)

#: Zero vector, the default for fingerprints of empty domains.
ZERO_TRIADS: tuple[float, ...] = (0.0,) * len(TRIAD_ORDER)


def _classify(succ: dict[str, set[str]], v: str, u: str, w: str) -> str:
    """Return the MAN class name for the ordered triple (v, u, w)."""
    code = (
        (1 if u in succ[v] else 0)
        + (2 if v in succ[u] else 0)
        + (4 if w in succ[v] else 0)
        + (8 if v in succ[w] else 0)
        + (16 if w in succ[u] else 0)
        + (32 if u in succ[w] else 0)
    )
    return _TRIAD_NAMES[_TRICODES[code] - 1]


def _adjacency(
    node_ids: set[str], edges: list[Edge], edge_type: EdgeType
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build (successor, undirected-neighbor) maps over intra-set edges of edge_type.

    Self-loops and edges touching nodes outside node_ids are ignored.
    """
    succ: dict[str, set[str]] = {n: set() for n in node_ids}
    nbrs: dict[str, set[str]] = {n: set() for n in node_ids}
    for e in edges:
        if e.type != edge_type or e.source == e.target:
            continue
        if e.source in succ and e.target in succ:
            succ[e.source].add(e.target)
            nbrs[e.source].add(e.target)
            nbrs[e.target].add(e.source)
    return succ, nbrs


def _connected_triples(
    node_ids: set[str], nbrs: dict[str, set[str]]
) -> Iterator[tuple[str, str, str]]:
    """Yield each connected unordered triple exactly once, as a sorted 3-tuple."""
    seen: set[tuple[str, str, str]] = set()
    for v in node_ids:
        for u in nbrs[v]:
            for w in nbrs[v] | nbrs[u]:
                if w in (v, u):
                    continue
                a, b, c = sorted((v, u, w))
                if (a, b, c) not in seen:
                    seen.add((a, b, c))
                    yield a, b, c


def triad_census(node_ids: set[str], edges: list[Edge], edge_type: EdgeType) -> dict[str, int]:
    """Count connected triads over intra-set edges of edge_type.

    Each unordered triple is counted exactly once. O(Σ deg(v)²) — fine at
    our scale (spec §3.6.3).
    """
    succ, nbrs = _adjacency(node_ids, edges, edge_type)
    counts: dict[str, int] = dict.fromkeys(TRIAD_ORDER, 0)
    # Every yielded triple has v-u adjacent and w adjacent to one of them,
    # so it is connected and the class is always one of the 13.
    for a, b, c in _connected_triples(node_ids, nbrs):
        counts[_classify(succ, a, b, c)] += 1
    return counts


def normalized_census(counts: dict[str, int]) -> tuple[float, ...]:
    """Return the census as a 13-tuple in TRIAD_ORDER, normalized to sum 1.

    An empty census normalizes to the all-zero vector ("no data", not NaN).
    """
    total = sum(counts.values())
    if total == 0:
        return ZERO_TRIADS
    return tuple(counts[name] / total for name in TRIAD_ORDER)


def tv_distance(
    t: tuple[float, ...],
    ideal: tuple[float, ...],
    weights: tuple[float, ...],
) -> tuple[float, list[tuple[str, float]]]:
    """Weighted total variation 0.5*sum(wi*|ti-ideali|) with per-triad decomposition.

    Returns (tv, contributions) where contributions pairs each TRIAD_ORDER
    name with its 0.5*wi*|ti-ideali| term (spec ss3.3: violations fall out of
    the metric instead of being approximated from it).
    """
    contribs = [
        (name, 0.5 * w * abs(a - b))
        for name, a, b, w in zip(TRIAD_ORDER, t, ideal, weights, strict=True)
    ]
    return sum((c for _, c in contribs), 0.0), contribs


#: MAN mutual-dyad count (M, the first digit of the MAN code) for each class,
#: aligned index-for-index with TRIAD_ORDER. Transpose-fixed mutual mass is the
#: anti-pattern signal (spec: health = acyclicity + antisymmetry).
_TANGLE_WEIGHTS: tuple[int, ...] = (0, 0, 0, 1, 1, 0, 0, 2, 1, 1, 1, 2, 3)

#: Max single-class weight (pure 300), used to normalize tangle into [0, 1].
_TANGLE_MAX_M = 3.0


def tangle_mass(census: tuple[float, ...]) -> float:
    """Normalized transpose-fixed (mutual) motif mass of a census, in [0, 1].

    Weights each normalized triad fraction by its MAN mutual-dyad count M and
    divides by the maximal M (3, pure 300). A pure DAG (only 021*/030T/030C)
    scores 0; a pure mutual mesh (300) scores 1. The empty census scores 0.
    """
    weighted = sum(w * t for w, t in zip(_TANGLE_WEIGHTS, census, strict=True))
    return weighted / _TANGLE_MAX_M
