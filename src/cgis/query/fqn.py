"""Suffix-based FQN resolution shared by CLI commands and MCP tools.

Fixes the prefix-mismatch UX wart (#145): a graph ingested from ``src``
holds ``cgis.*`` FQNs while users (and agents) often pass ``src.cgis.*``
or bare names. A unique dot-boundary suffix match resolves silently;
ambiguity surfaces the candidates instead of a bare "not found".
"""

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from cgis.storage.sqlite_store import SQLiteStore

_CANDIDATE_LIMIT = 10
_PROBE_LIMIT = _CANDIDATE_LIMIT + 1  # one extra to detect truncation


@dataclass(frozen=True)
class FqnResolution:
    """Outcome of resolving a possibly-partial FQN against the graph.

    ``resolved`` is set when there is exactly one match (exact or suffix).
    ``candidates`` lists up to ``_CANDIDATE_LIMIT`` alternatives when the
    suffix is ambiguous. ``via_suffix`` is True when the caller's name is not
    the node's id — matched by dot-boundary suffix search, or with leading
    segments dropped (#568) — so the answer should say what it resolved to.
    ``truncated`` is True when the candidate list was capped because more
    than ``_CANDIDATE_LIMIT`` nodes matched; in that case the caller should
    prompt the user to refine the name.
    """

    resolved: str | None
    candidates: list[str] = field(default_factory=list)
    via_suffix: bool = False
    truncated: bool = False


def is_package_prefix(store: SQLiteStore, fqn: str) -> bool:
    """True when `fqn` names a package: FILE nodes live under it (#487).

    Checked before `resolve_fqn`, not after it fails. Not because resolution would
    answer with the empty `__init__` file — `get_structural_graph` re-checks on the
    resolved id, so that case lands right either way — but because a prefix can
    *suffix-resolve to a different tree*: on one real backend `api.dependencies`
    resolves to `app.api.dependencies`, answering about a package the caller did
    not name.

    A module has symbols under its id but no files, so it is never taken for a
    package; a file whose id is a prefix of other files (`utils.ts` beside
    `utils/`) keeps its own members, which `_package_graph` merges in.
    """
    return bool(store.files_under(fqn))


def _path_prefixes(store: SQLiteStore) -> set[str]:
    """Dotted tails of the directory the graph's FQNs start below.

    For a graph ingested from ``/w/ownima-backend/app`` these are ``app``,
    ``ownima-backend.app`` and ``w.ownima-backend.app``; a ``--source-root``
    extends the base it names. Empty for a graph that predates the recorded
    root (#175) — nothing is stripped there.
    """
    state = store.get_ingest_state()
    if state is None:
        return set()
    # Backslashes as separators, in the root as in a source root (`BaseExtractor`):
    # a graph ingested on Windows and read elsewhere would otherwise strip nothing.
    root = PurePosixPath(state[0].replace("\\", "/"))
    options = store.get_ingest_options()
    sources = [source.replace("\\", "/") for source in (options[0] if options else [])]
    bases = [root, *(root / source for source in sources)]
    return {".".join(base.parts[start:]) for base in bases for start in range(1, len(base.parts))}


def without_path_prefix(store: SQLiteStore, prefix: str) -> str | None:
    """`prefix` with a leading directory above the graph dropped, or None (#584).

    The `fqn_prefix` of `cgis_find_symbol` scopes a search rather than naming a
    node, so unlike `_without_leading_segments` the rest need not exist as a
    node: `src.cgis` on a graph ingested from `src/` is the prefix `cgis`.
    Something must remain — dropping the whole prefix would widen the search
    to the entire graph, which is not what was asked.
    """
    prefixes = _path_prefixes(store)
    parts = prefix.split(".")
    for start in range(1, len(parts)):
        if ".".join(parts[:start]) in prefixes:
            return ".".join(parts[start:])
    return None


def _without_leading_segments(store: SQLiteStore, fqn: str) -> str | None:
    """The node ``fqn`` names once a path prefix above the graph is dropped (#568).

    An agent builds an FQN from the path it sees — ``ownima-backend/app/domains/
    finance/service.py`` gives ``app.domains.finance.service.credit_wallet`` —
    while a graph ingested from ``app`` names it ``domains.finance.service.
    credit_wallet``. That name is longer than the node's id, so suffix search
    cannot help: every forced session of the 2026-10-13 A/B opened with a
    not-found on it.

    Only the directories the graph was ingested below may be dropped, and the
    rest must match exactly. Dropping any leading segment resolved
    ``app.nope.service.credit_wallet`` to an unrelated top-level
    ``service.credit_wallet`` on owner-api — a wrong symbol, answered
    confidently. With the drop pinned to the root, what remains starts at the
    graph's own top level, so one segment is safe too: ``app.domains`` is the
    package ``domains``.
    """
    prefixes = _path_prefixes(store)
    parts = fqn.split(".")
    for start in range(1, len(parts)):
        candidate = ".".join(parts[start:])
        if ".".join(parts[:start]) in prefixes and store.get_node(candidate):
            return candidate
    return None


def resolve_fqn(store: SQLiteStore, fqn: str) -> FqnResolution:
    """Resolve ``fqn`` exactly, or by unique dot-boundary suffix match.

    Exact hit resolves as-is (``via_suffix=False``); a single suffix hit
    resolves to the full FQN with ``via_suffix=True``; several hits return
    ``candidates`` (capped at ``_CANDIDATE_LIMIT``); no hit returns an empty
    resolution.  When the result set is larger than ``_CANDIDATE_LIMIT`` the
    ``truncated`` flag is set to signal that the listing is incomplete.

    With no suffix hit either, a leading path prefix the graph was ingested
    below is dropped and the rest matched exactly (``_without_leading_segments``).

    Empty or whitespace-only input returns an unresolved ``FqnResolution``
    immediately without touching the store.

    Exact-match policy lives here, not in ``SQLiteStore.find_nodes_by_suffix``,
    so the storage layer stays a pure suffix search with no intra-domain call
    chain (enforced by the pure_utility self-drift guardrail, #145).
    """
    fqn = fqn.strip()
    if not fqn:
        return FqnResolution(resolved=None)
    exact = store.get_node(fqn)
    if exact:
        return FqnResolution(resolved=fqn)
    matches = store.find_nodes_by_suffix(fqn, limit=_PROBE_LIMIT)
    if not matches:
        stripped = _without_leading_segments(store, fqn)
        return FqnResolution(resolved=stripped, via_suffix=stripped is not None)
    if len(matches) == 1:
        return FqnResolution(resolved=matches[0].id, via_suffix=True)
    truncated = len(matches) > _CANDIDATE_LIMIT
    candidates = [n.id for n in matches[:_CANDIDATE_LIMIT]]
    return FqnResolution(resolved=None, candidates=candidates, truncated=truncated)
