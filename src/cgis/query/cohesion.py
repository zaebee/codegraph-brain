"""Package-cohesion analysis: community detection over a package's file graph (#242).

Pure logic, no I/O. The intra-package file graph is undirected and weighted; edge
reconciliation is done in-memory by dot-boundary FQN suffix so the result does not
depend on the ingest root (src/ vs src/cgis/ — the #242 load-bearing fix).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from cgis.core.models import Edge, EdgeType, Node, NodeType

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping

_FILE_TYPES = frozenset({NodeType.FILE, NodeType.MODULE})


@dataclass(frozen=True)
class FileGraph:
    """Undirected, weighted intra-package file graph.

    ``files`` lists every file under the package prefix (sorted, including
    isolated leaves). ``adj`` is a symmetric weighted adjacency: ``adj[a][b]``
    == ``adj[b][a]`` is the aggregated import (and optionally call) weight
    between two files. Isolated files have no ``adj`` entry.
    """

    files: tuple[str, ...]
    adj: dict[str, dict[str, float]]


def _under(fqn: str, prefix: str) -> bool:
    """Return True iff fqn is the prefix itself or a dot-boundary child of it.

    An empty prefix matches nothing — the service normalizes a missing/blank
    prefix to a no_signal report before this is ever reached.
    """
    return bool(prefix) and (fqn == prefix or fqn.startswith(prefix + "."))


def _build_suffix_index(file_ids: list[str]) -> dict[str, str]:
    """Map every dot-boundary suffix of each file id to that id.

    A suffix shared by two files maps to the sentinel ``""`` (ambiguous — never
    reconcile to it). Used to link an import target written with a different
    root (``cgis.p.b``) back to the in-graph file (``p.b``).
    """
    index: dict[str, str] = {}
    for fid in file_ids:
        parts = fid.split(".")
        for i in range(len(parts)):
            suffix = ".".join(parts[i:])
            index[suffix] = "" if suffix in index and index[suffix] != fid else fid
    return index


def _resolve_to_file(node_id: str, file_set: set[str], suffix_index: dict[str, str]) -> str | None:
    """Resolve an edge endpoint to a file under the prefix, or None.

    Exact membership wins; otherwise the longest dot-boundary suffix of
    ``node_id`` that uniquely names a file. Ambiguous or absent → None.
    """
    if node_id in file_set:
        return node_id
    parts = node_id.split(".")
    for i in range(len(parts)):
        hit = suffix_index.get(".".join(parts[i:]))
        if hit:
            return hit
    return None


def build_file_graph(
    nodes: list[Node], edges: list[Edge], prefix: str, with_calls: bool
) -> FileGraph:
    """Build the undirected weighted file graph for the package at ``prefix``.

    Files = FILE/MODULE nodes under ``prefix``. Edges = IMPORTS (and CALLS when
    ``with_calls``) whose BOTH endpoints reconcile to distinct files under the
    prefix; weights aggregate. Endpoints are reconciled by dot-boundary suffix
    so a target written with a different root still links (root-agnostic, #242).
    """
    file_ids = sorted(n.id for n in nodes if n.type in _FILE_TYPES and _under(n.id, prefix))
    file_set = set(file_ids)
    suffix_index = _build_suffix_index(file_ids)
    wanted = {EdgeType.IMPORTS} | ({EdgeType.CALLS} if with_calls else set())

    adj: dict[str, dict[str, float]] = {}
    for e in edges:
        if e.type not in wanted:
            continue
        a = _resolve_to_file(e.source, file_set, suffix_index)
        b = _resolve_to_file(e.target, file_set, suffix_index)
        if a is None or b is None or a == b:
            continue
        adj.setdefault(a, {})[b] = adj.setdefault(a, {}).get(b, 0.0) + e.weight
        adj.setdefault(b, {})[a] = adj.setdefault(b, {}).get(a, 0.0) + e.weight
    return FileGraph(files=tuple(file_ids), adj=adj)


_MIN_GAIN = 1e-12  # ignore non-positive / floating-noise merges


def _modularity(
    graph: FileGraph,
    communities: list[list[str]],
    deg: dict[str, float],
    m2: float,
) -> float:
    """Newman modularity Q for a partition (m2 == 2m == sum of weighted degrees)."""
    q = 0.0
    for c in communities:
        members = set(c)
        l_c = sum(w for f in c for g, w in graph.adj.get(f, {}).items() if g in members)
        d_c = sum(deg[f] for f in c)
        q += l_c / m2 - (d_c / m2) ** 2
    return q


def _best_merge(
    graph: FileGraph,
    members: dict[str, set[str]],
    deg: dict[str, float],
    m2: float,
) -> tuple[str, str] | None:
    """Return the (c1, c2) pair with the highest positive delta-Q, or None.

    Iterates sorted label pairs for lexicographic tie-breaking (deterministic).
    Returns None when no merge would improve modularity by more than _MIN_GAIN.
    """
    best_gain: float = _MIN_GAIN
    best_pair: tuple[str, str] | None = None
    labels = sorted(members)
    # Precompute each community's normalized degree once (O(V)) rather than
    # re-summing it inside the O(K²) pair scan.
    a_of = {c: sum(deg[f] for f in members[c]) / m2 for c in labels}
    for i, c1 in enumerate(labels):
        for c2 in labels[i + 1 :]:
            e_ij = (
                sum(graph.adj.get(f, {}).get(g, 0.0) for f in members[c1] for g in members[c2]) / m2
            )
            if e_ij <= 0.0:  # disconnected (weights are non-negative); merging can't raise Q
                continue
            dq = 2 * (e_ij - a_of[c1] * a_of[c2])
            if dq > best_gain:
                best_gain, best_pair = dq, (c1, c2)
    return best_pair


def greedy_modularity(graph: FileGraph) -> tuple[list[list[str]], float]:
    """Greedy (Clauset-Newman-Moore) community detection; returns (communities, Q).

    Each file starts in its own community; the connected pair with the maximum
    positive delta-Q = 2*(e_ij - a_i*a_j) is merged until no merge improves Q.
    Ties break on the lexicographically smallest label pair (deterministic).
    Isolated files stay singletons and contribute 0 to Q. Communities are
    returned sorted (members sorted, then the list sorted).
    """
    files = list(graph.files)
    if not files:
        return [], 0.0
    deg = {f: sum(graph.adj.get(f, {}).values()) for f in files}
    m2 = sum(deg.values())
    if m2 <= 0.0:  # all isolated (degree sum is non-negative)
        return [[f] for f in files], 0.0

    members: dict[str, set[str]] = {f: {f} for f in files}
    while len(members) > 1:
        best_pair = _best_merge(graph, members, deg, m2)
        if best_pair is None:
            break
        c1, c2 = best_pair
        members[c1] |= members.pop(c2)

    communities = sorted(sorted(s) for s in members.values())
    return communities, _modularity(graph, communities, deg, m2)


Direction = Literal["under_split", "over_split", "matched"]


def _entropy(labels: Mapping[str, Hashable]) -> float:
    """Shannon entropy (nats) of a partition's label distribution."""
    n = len(labels)
    counts = Counter(labels.values())
    return -sum((c / n) * math.log(c / n) for c in counts.values())


def _mutual_information(a: Mapping[str, Hashable], b: Mapping[str, Hashable]) -> float:
    """Mutual information (nats) between two partitions over the same key set."""
    n = len(a)
    ca, cb = Counter(a.values()), Counter(b.values())
    joint = Counter((a[k], b[k]) for k in a)
    mi = 0.0
    for (x, y), nxy in joint.items():
        p_xy = nxy / n
        mi += p_xy * math.log(p_xy / ((ca[x] / n) * (cb[y] / n)))
    return mi


def partition_divergence(p_comm: Mapping[str, Hashable], p_dir: Mapping[str, Hashable]) -> float:
    """1 - NMI between two partitions of the same file set, in [0, 1].

    NMI = I(X;Y) / mean(H(X), H(Y)); defined as 1.0 (so D = 0) when both
    partitions are trivial (single cluster each). A non-trivial partition
    against a trivial one has MI 0 -> NMI 0 -> D = 1 (the flat-package case).
    """
    h_a, h_b = _entropy(p_comm), _entropy(p_dir)
    if h_a <= 0.0 and h_b <= 0.0:  # both trivial (entropy is non-negative; -0.0 counts)
        return 0.0
    nmi = _mutual_information(p_comm, p_dir) / ((h_a + h_b) / 2)
    return 1.0 - nmi


def layout_direction(p_comm: Mapping[str, Hashable], p_dir: Mapping[str, Hashable]) -> Direction:
    """Direction of the layout/community mismatch by distinct-group count.

    ``under_split`` when the directory layout is flatter than the communities
    (fewer dir groups than communities); ``over_split`` when finer; ``matched``
    when equal.
    """
    n_dir, n_comm = len(set(p_dir.values())), len(set(p_comm.values()))
    if n_dir < n_comm:
        return "under_split"
    if n_dir > n_comm:
        return "over_split"
    return "matched"


Verdict = Literal["split", "consolidate", "aligned", "leave", "borderline", "no_signal"]

#: Default thresholds (cross-validated for Q; divergence is provisional, #242 spec).
THRESHOLDS: dict[str, float] = {"split": 0.35, "leave": 0.25, "divergence": 0.2}


def classify_verdict(
    *, q: float, d: float, direction: Direction, thresholds: Mapping[str, float]
) -> Verdict:
    """Verdict from modularity Q, divergence D, and mismatch direction.

    ``no_signal`` is decided upstream (no files / no edges); this maps a scored
    package. Gated on BOTH Q (structure is real) and D (layout disagrees), with
    direction disambiguating split (flatter layout) from consolidate (finer).
    """
    if q < thresholds["leave"]:
        return "leave"
    if d < thresholds["divergence"]:
        return "aligned"
    if q >= thresholds["split"]:
        if direction == "under_split":
            return "split"
        if direction == "over_split":
            return "consolidate"
    return "borderline"
