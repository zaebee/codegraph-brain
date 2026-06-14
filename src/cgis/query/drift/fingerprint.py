"""PatternFingerprint dataclass and FingerprintExtractor."""

from collections import Counter
from dataclasses import dataclass, field

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.analysis.health import HealthScorer
from cgis.query.drift._scc import build_adjacency, tarjan_scc
from cgis.query.drift.triads import ZERO_TRIADS, normalized_census, tangle_mass, triad_census
from cgis.storage.sqlite_store import SQLiteStore

RAW_CALL_PREFIX = "raw_call:"

_HUB_FAN_IN_THRESHOLD = 2
_STAR_FAN_OUT_THRESHOLD = 3
_ROUTER_FAN_OUT_THRESHOLD = 2


@dataclass(frozen=True)
class PatternFingerprint:
    """Structural fingerprint for a domain: seven v1 counters plus two v2 census vectors."""

    domain: str

    hub_count: int
    star_count: int
    chain_len: float
    dag_depth: int
    router_count: int

    cycle_ratio: float
    unresolved_ratio: float

    # Fingerprint v2 (spec §3.2): normalized 13-triad census per layer,
    # ordered by triads.TRIAD_ORDER. Zero vector = empty layer ("no data").
    t_imports: tuple[float, ...] = field(default=ZERO_TRIADS)
    t_calls: tuple[float, ...] = field(default=ZERO_TRIADS)

    # How many graph nodes / intra-domain edges the selector matched.
    # Defaults are 1 ("hand-built fingerprints are assumed measurable",
    # spec Amendment 1); only extract() produces real zeros. 0 nodes means
    # the fqn_prefix selected nothing; 0 edges with >0 nodes means there is
    # no structure to score (isolated symbols / alias-only matches).
    node_count: int = 1
    edge_count: int = 1

    @property
    def tangle_ratio(self) -> float:
        """Worst-layer normalized mutual-motif mass, in [0, 1] (spec #186).

        max over the IMPORTS and CALLS census of tangle_mass — a hard hygiene
        signal: a breach in either layer is a breach. 0 for empty or
        antisymmetric (no-mutual-dyad) layers.

        The CALLS layer is faded by the confidence discount
        ``clip(1 - unresolved_ratio)`` before the max (#244), mirroring the
        drift-distance path (``_clip_discount`` / ``_score_v2``): ``t_calls`` is a
        census over RESOLVED intra-domain calls only, so a sparse/mostly-unresolved
        call graph would otherwise over-report calls-tangle and trip the gate on
        thin evidence. The IMPORTS layer (always resolved) is never discounted.
        """
        calls_discount = max(0.0, min(1.0 - self.unresolved_ratio, 1.0))
        return max(tangle_mass(self.t_imports), calls_discount * tangle_mass(self.t_calls))


def _in_domain(fqn: str, prefix: str) -> bool:
    """Return True iff fqn is the prefix itself or a child of it (segment-boundary aware)."""
    return fqn == prefix or fqn.startswith(prefix + ".")


def _root_depths(domain_ids: set[str], edges: list[Edge], edge_type: EdgeType) -> list[int]:
    """Longest-path depth from each root node along edges of edge_type within the domain.

    A root is a domain node with outgoing edges of the given type but no incoming ones.
    Uses memoized DFS (cycle-safe); roots are sorted so results are deterministic.
    """
    adj: dict[str, list[str]] = {}
    has_incoming: set[str] = set()
    for e in edges:
        if e.type == edge_type:
            adj.setdefault(e.source, []).append(e.target)
            has_incoming.add(e.target)

    roots = sorted(n for n in domain_ids if n not in has_incoming and n in adj)
    if not roots:
        return []

    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def dfs(u: str) -> int:
        if u in memo:
            return memo[u]
        if u in visiting:
            return 0
        visiting.add(u)
        val = max((1 + dfs(v) for v in adj.get(u, [])), default=0)
        visiting.remove(u)
        memo[u] = val
        return val

    return [dfs(root) for root in roots]


def _avg_chain_length(domain_ids: set[str], internal_calls: list[Edge]) -> float:
    """Average longest-path depth from each source node along CALLS edges within the domain."""
    depths = _root_depths(domain_ids, internal_calls, EdgeType.CALLS)
    return sum(depths) / len(depths) if depths else 0.0


def _max_dag_depth(domain_ids: set[str], internal_edges: list[Edge]) -> int:
    """Max longest-path depth along IMPORTS edges within the domain (from import-root nodes)."""
    depths = _root_depths(domain_ids, internal_edges, EdgeType.IMPORTS)
    return max(depths) if depths else 0


def _count_routers(domain_node_ids: set[str], all_edges: list[Edge]) -> int:
    """Count domain nodes with fan_out > 2 whose CALLS targets don't import them."""
    imported_by: dict[str, set[str]] = {}
    calls_by_source: dict[str, list[str]] = {}
    for e in all_edges:
        if e.type == EdgeType.IMPORTS:
            imported_by.setdefault(e.target, set()).add(e.source)
        elif e.type == EdgeType.CALLS:
            calls_by_source.setdefault(e.source, []).append(e.target)

    router_count = 0
    for node_id in domain_node_ids:
        calls_targets = calls_by_source.get(node_id, [])
        if len(calls_targets) <= _ROUTER_FAN_OUT_THRESHOLD:
            continue
        node_importers = imported_by.get(node_id, set())
        if not (set(calls_targets) & node_importers):
            router_count += 1

    return router_count


class FingerprintExtractor:
    """Compute a PatternFingerprint for a given FQN domain prefix from a SQLiteStore."""

    def __init__(self, store: SQLiteStore | None) -> None:
        """Accept an open SQLiteStore, or None when built via from_graph()."""
        self._store = store
        self._cache: tuple[list[Node], list[Edge]] | None = None

    @classmethod
    def from_graph(cls, nodes: list[Node], edges: list[Edge]) -> "FingerprintExtractor":
        """Build an extractor over an in-memory graph — no SQLiteStore involved.

        The inputs are copied defensively, so later mutation of the caller's
        lists cannot change what extract() measures (matches the store path,
        which returns fresh lists on every fetch).
        """
        inst = cls(None)
        inst._cache = (HealthScorer(nodes, edges).enrich(), list(edges))
        return inst

    def _loaded(self) -> tuple[list[Node], list[Edge]]:
        """Return (enriched_nodes, all_edges), fetching from the store once and caching."""
        if self._cache is None:
            if self._store is None:
                msg = "FingerprintExtractor needs a store or a from_graph() preload."
                raise RuntimeError(msg)
            all_nodes = self._store.get_all_nodes()
            all_edges = self._store.get_all_edges()
            self._cache = (HealthScorer(all_nodes, all_edges).enrich(), all_edges)
        return self._cache

    def extract(self, fqn_prefix: str) -> PatternFingerprint:
        """Return the structural fingerprint for all nodes under fqn_prefix."""
        enriched_all, all_edges = self._loaded()

        domain_nodes = [n for n in enriched_all if _in_domain(n.id, fqn_prefix)]
        if not domain_nodes:
            return PatternFingerprint(
                domain=fqn_prefix,
                hub_count=0,
                star_count=0,
                chain_len=0.0,
                dag_depth=0,
                router_count=0,
                cycle_ratio=0.0,
                unresolved_ratio=0.0,
                node_count=0,
                edge_count=0,
            )

        domain_ids = {n.id for n in domain_nodes}

        internal_edges = [e for e in all_edges if e.source in domain_ids and e.target in domain_ids]
        domain_outgoing = [e for e in all_edges if e.source in domain_ids]

        # Fan-in/fan-out over intra-domain CALLS only — external targets
        # (stdlib, other domains) would otherwise inflate star/hub counts.
        fan_in = Counter(e.target for e in internal_edges if e.type == EdgeType.CALLS)
        fan_out = Counter(e.source for e in internal_edges if e.type == EdgeType.CALLS)

        hub_count = sum(
            1 for n in domain_nodes if fan_in[n.id] > _HUB_FAN_IN_THRESHOLD and fan_out[n.id] == 0
        )
        star_count = sum(
            1 for n in domain_nodes if fan_out[n.id] > _STAR_FAN_OUT_THRESHOLD and fan_in[n.id] <= 1
        )
        chain_len = _avg_chain_length(domain_ids, internal_edges)
        dag_depth = _max_dag_depth(domain_ids, internal_edges)
        router_count = _count_routers(domain_ids, all_edges)

        cycle_ratio = self._intra_domain_cycle_ratio(domain_nodes, all_edges)

        calls_edges = [e for e in domain_outgoing if e.type == EdgeType.CALLS]
        raw_calls = [e for e in calls_edges if e.target.startswith(RAW_CALL_PREFIX)]
        unresolved_ratio = len(raw_calls) / len(calls_edges) if calls_edges else 0.0

        t_imports = normalized_census(triad_census(domain_ids, internal_edges, EdgeType.IMPORTS))
        t_calls = normalized_census(triad_census(domain_ids, internal_edges, EdgeType.CALLS))

        return PatternFingerprint(
            domain=fqn_prefix,
            hub_count=hub_count,
            star_count=star_count,
            chain_len=chain_len,
            dag_depth=dag_depth,
            router_count=router_count,
            cycle_ratio=cycle_ratio,
            unresolved_ratio=unresolved_ratio,
            t_imports=t_imports,
            t_calls=t_calls,
            node_count=len(domain_nodes),
            edge_count=len(internal_edges),
        )

    def _intra_domain_cycle_ratio(self, domain_nodes: list[Node], all_edges: list[Edge]) -> float:
        """Blast radius of the domain's OWN import cycles (spec §2.1, #176).

        Tarjan SCC over IMPORTS edges whose endpoints are both FILE/MODULE
        nodes of this domain; the ratio counts domain nodes living in files
        that participate in an intra-domain cycle. Single-file domains are
        0.0 by construction; cross-domain cycles are the quotient layer's
        concern and never count here.
        """
        file_types = {NodeType.FILE, NodeType.MODULE}
        domain_files = {n.id for n in domain_nodes if n.type in file_types}
        if len(domain_files) < 2:
            return 0.0
        adj = build_adjacency(all_edges, frozenset({EdgeType.IMPORTS}))
        adj = {
            k: [v for v in vs if v in domain_files] for k, vs in adj.items() if k in domain_files
        }
        cyclic_files = {n for scc in tarjan_scc(adj) if len(scc) > 1 for n in scc}
        if not cyclic_files:
            return 0.0
        cyclic_paths = {n.file_path for n in domain_nodes if n.id in cyclic_files}
        cycle_count = sum(1 for n in domain_nodes if n.file_path in cyclic_paths)
        return cycle_count / len(domain_nodes)
