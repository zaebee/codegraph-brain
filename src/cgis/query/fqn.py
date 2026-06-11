"""Suffix-based FQN resolution shared by CLI commands and MCP tools.

Fixes the prefix-mismatch UX wart (#145): a graph ingested from ``src``
holds ``cgis.*`` FQNs while users (and agents) often pass ``src.cgis.*``
or bare names. A unique dot-boundary suffix match resolves silently;
ambiguity surfaces the candidates instead of a bare "not found".
"""

from dataclasses import dataclass, field

from cgis.storage.sqlite_store import SQLiteStore


@dataclass(frozen=True)
class FqnResolution:
    """Outcome of resolving a possibly-partial FQN against the graph."""

    resolved: str | None
    candidates: list[str] = field(default_factory=list)
    via_suffix: bool = False


def resolve_fqn(store: SQLiteStore, fqn: str) -> FqnResolution:
    """Resolve ``fqn`` exactly, or by unique dot-boundary suffix match.

    Exact hit resolves as-is (``via_suffix=False``); a single suffix hit
    resolves to the full FQN with ``via_suffix=True``; several hits return
    ``candidates``; no hit returns an empty resolution.

    Exact-match policy lives here, not in ``SQLiteStore.find_nodes_by_suffix``,
    so the storage layer stays a pure suffix search with no intra-domain call
    chain (enforced by the pure_utility self-drift guardrail, #145).
    """
    exact = store.get_node(fqn)
    if exact:
        return FqnResolution(resolved=fqn)
    matches = store.find_nodes_by_suffix(fqn)
    if not matches:
        return FqnResolution(resolved=None)
    if len(matches) == 1:
        return FqnResolution(resolved=matches[0].id, via_suffix=True)
    return FqnResolution(resolved=None, candidates=[n.id for n in matches])
