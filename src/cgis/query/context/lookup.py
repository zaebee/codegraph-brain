"""Answer a batch of symbol lookups by database path (#246 experiment C).

For a caller that holds a path, not a store — the guardian's two-pass finder,
which names symbols it wants to read. Each lookup resolves like `cgis_context`
(exact, then unique suffix) and comes back as one of two kinds: `context`, the
symbol's brief from `build_context`, or `callers`, its upstream as an indented
tree. A name that does not resolve is answered, not dropped, so the caller can
tell "the graph has nothing" from "the lookup was lost".
"""

from collections.abc import Sequence
from typing import Literal, NamedTuple

from cgis.query.context.context_service import build_context
from cgis.query.engine import QueryEngine
from cgis.query.fqn import resolve_fqn
from cgis.query.render.text import graph_to_text
from cgis.storage.sqlite_store import SQLiteStore

LookupKind = Literal["context", "callers"]

_CALLERS_DEPTH = 2
_CALLERS_MAX_NODES = 40


class SymbolAnswer(NamedTuple):
    """The FQN a lookup resolved to (None when it did not) and the answer text."""

    resolved: str | None
    text: str


def _answer(store: SQLiteStore, symbol: str, kind: LookupKind, source_root: str) -> SymbolAnswer:
    """One lookup against an open store."""
    res = resolve_fqn(store, symbol)
    if res.resolved is None:
        if res.candidates:
            shown = ", ".join(f"`{c}`" for c in res.candidates)
            more = " (more exist)" if res.truncated else ""
            return SymbolAnswer(
                None, f"unresolved: the name is ambiguous; candidates: {shown}{more}"
            )
        return SymbolAnswer(None, "unresolved: no symbol of that name is in the graph")
    if kind == "context":
        return SymbolAnswer(
            res.resolved, build_context(store, res.resolved, depth=1, source_root=source_root)
        )
    result = QueryEngine(store).get_impact_result(
        res.resolved, max_depth=_CALLERS_DEPTH, with_coverage=False, max_nodes=_CALLERS_MAX_NODES
    )
    if len(result.nodes) <= 1:
        return SymbolAnswer(
            res.resolved, "no static callers found (dispatch through a framework is invisible)"
        )
    return SymbolAnswer(
        res.resolved, graph_to_text(res.resolved, result.nodes, result.edges, downstream=False)
    )


def answer_lookups(
    db_path: str, lookups: Sequence[tuple[str, LookupKind]], source_root: str
) -> list[SymbolAnswer]:
    """Each `(symbol, kind)` answered from the graph at `db_path`, in order.

    ``source_root`` locates source files for `context` answers, as for
    `cgis_context`.
    """
    with SQLiteStore(db_path) as store:
        return [_answer(store, symbol, kind, source_root) for symbol, kind in lookups]
