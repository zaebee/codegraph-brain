"""PatternFingerprint dataclass and FingerprintExtractor."""

from dataclasses import dataclass

from cgis.core.models import Edge, EdgeType, Node
from cgis.query.health import HealthScorer
from cgis.storage.sqlite_store import SQLiteStore

RAW_CALL_PREFIX = "raw_call:"

_HUB_FAN_IN_THRESHOLD = 2
_STAR_FAN_OUT_THRESHOLD = 3
_ROUTER_FAN_OUT_THRESHOLD = 2


@dataclass(frozen=True)
class PatternFingerprint:
    """Seven-component structural fingerprint for a domain."""

    domain: str

    hub_count: int
    star_count: int
    chain_len: float
    dag_depth: int
    router_count: int

    cycle_ratio: float
    unresolved_ratio: float


def _in_domain(fqn: str, prefix: str) -> bool:
    """Return True iff fqn is the prefix itself or a child of it (segment-boundary aware)."""
    return fqn == prefix or fqn.startswith(prefix + ".")


def _avg_chain_length(domain_ids: set[str], internal_calls: list[Edge]) -> float:
    """Average longest-path depth from each source node along CALLS edges within the domain."""
    adj: dict[str, list[str]] = {}
    has_incoming: set[str] = set()
    for e in internal_calls:
        if e.type == EdgeType.CALLS:
            adj.setdefault(e.source, []).append(e.target)
            has_incoming.add(e.target)

    sources = sorted(n for n in domain_ids if n not in has_incoming and n in adj)
    if not sources:
        return 0.0

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

    depths = [dfs(src) for src in sources]
    return sum(depths) / len(depths)


def _max_dag_depth(domain_ids: set[str], internal_edges: list[Edge]) -> int:
    """Max longest-path depth along IMPORTS edges within the domain (from import-root nodes)."""
    adj: dict[str, list[str]] = {}
    has_incoming: set[str] = set()
    for e in internal_edges:
        if e.type == EdgeType.IMPORTS:
            adj.setdefault(e.source, []).append(e.target)
            has_incoming.add(e.target)

    roots = sorted(n for n in domain_ids if n not in has_incoming and n in adj)
    if not roots:
        return 0

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

    return max(dfs(root) for root in roots)


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

    def __init__(self, store: SQLiteStore) -> None:
        """Accept an open SQLiteStore (must already be connected)."""
        self._store = store
        self._cache: tuple[list[Node], list[Edge]] | None = None

    def _loaded(self) -> tuple[list[Node], list[Edge]]:
        """Return (enriched_nodes, all_edges), fetching from the store once and caching."""
        if self._cache is None:
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
            )

        domain_ids = {n.id for n in domain_nodes}

        internal_edges = [e for e in all_edges if e.source in domain_ids and e.target in domain_ids]
        domain_outgoing = [e for e in all_edges if e.source in domain_ids]

        hub_count = sum(
            1
            for n in domain_nodes
            if n.metadata.get("fan_in", 0) > _HUB_FAN_IN_THRESHOLD
            and n.metadata.get("fan_out", 0) == 0
        )
        star_count = sum(
            1
            for n in domain_nodes
            if n.metadata.get("fan_out", 0) > _STAR_FAN_OUT_THRESHOLD
            and n.metadata.get("fan_in", 0) <= 1
        )
        chain_len = _avg_chain_length(domain_ids, internal_edges)
        dag_depth = _max_dag_depth(domain_ids, internal_edges)
        router_count = _count_routers(domain_ids, all_edges)

        cycle_count = sum(1 for n in domain_nodes if n.metadata.get("in_cycle", False))
        cycle_ratio = cycle_count / len(domain_nodes)

        calls_edges = [e for e in domain_outgoing if e.type == EdgeType.CALLS]
        raw_calls = [e for e in calls_edges if e.target.startswith(RAW_CALL_PREFIX)]
        unresolved_ratio = len(raw_calls) / len(calls_edges) if calls_edges else 0.0

        return PatternFingerprint(
            domain=fqn_prefix,
            hub_count=hub_count,
            star_count=star_count,
            chain_len=chain_len,
            dag_depth=dag_depth,
            router_count=router_count,
            cycle_ratio=cycle_ratio,
            unresolved_ratio=unresolved_ratio,
        )
