"""Suffix-based FQN resolution shared by CLI commands and MCP tools.

Fixes the prefix-mismatch UX wart (#145): a graph ingested from ``src``
holds ``cgis.*`` FQNs while users (and agents) often pass ``src.cgis.*``
or bare names. A unique dot-boundary suffix match resolves silently;
ambiguity surfaces the candidates instead of a bare "not found".
"""

from dataclasses import dataclass, field

from cgis.storage.sqlite_store import SQLiteStore

_CANDIDATE_LIMIT = 10
_PROBE_LIMIT = _CANDIDATE_LIMIT + 1  # one extra to detect truncation


@dataclass(frozen=True)
class FqnResolution:
    """Outcome of resolving a possibly-partial FQN against the graph.

    ``resolved`` is set when there is exactly one match (exact or suffix).
    ``candidates`` lists up to ``_CANDIDATE_LIMIT`` alternatives when the
    suffix is ambiguous. ``via_suffix`` is True when the caller passed a
    partial name that was matched by dot-boundary suffix search.
    ``truncated`` is True when the candidate list was capped because more
    than ``_CANDIDATE_LIMIT`` nodes matched; in that case the caller should
    prompt the user to refine the name.
    """

    resolved: str | None
    candidates: list[str] = field(default_factory=list)
    via_suffix: bool = False
    truncated: bool = False


def resolve_fqn(store: SQLiteStore, fqn: str) -> FqnResolution:
    """Resolve ``fqn`` exactly, or by unique dot-boundary suffix match.

    Exact hit resolves as-is (``via_suffix=False``); a single suffix hit
    resolves to the full FQN with ``via_suffix=True``; several hits return
    ``candidates`` (capped at ``_CANDIDATE_LIMIT``); no hit returns an empty
    resolution.  When the result set is larger than ``_CANDIDATE_LIMIT`` the
    ``truncated`` flag is set to signal that the listing is incomplete.

    Exact-match policy lives here, not in ``SQLiteStore.find_nodes_by_suffix``,
    so the storage layer stays a pure suffix search with no intra-domain call
    chain (enforced by the pure_utility self-drift guardrail, #145).
    """
    exact = store.get_node(fqn)
    if exact:
        return FqnResolution(resolved=fqn)
    matches = store.find_nodes_by_suffix(fqn, limit=_PROBE_LIMIT)
    if not matches:
        return FqnResolution(resolved=None)
    if len(matches) == 1:
        return FqnResolution(resolved=matches[0].id, via_suffix=True)
    truncated = len(matches) > _CANDIDATE_LIMIT
    candidates = [n.id for n in matches[:_CANDIDATE_LIMIT]]
    return FqnResolution(resolved=None, candidates=candidates, truncated=truncated)
