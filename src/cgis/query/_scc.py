"""Shared Tarjan SCC and adjacency utilities for the query package."""

from cgis.core.models import Edge, EdgeType


def build_adjacency(edges: list[Edge], allowed_types: frozenset[EdgeType]) -> dict[str, list[str]]:
    """Build a directed adjacency list from edges matching allowed_types."""
    adj: dict[str, list[str]] = {}
    for edge in edges:
        if edge.type not in allowed_types:
            continue
        if edge.target.startswith(("raw_call:", "raw_import:")):
            continue
        adj.setdefault(edge.source, []).append(edge.target)
    return adj


def _pop_scc(v: str, stack: list[str], on_stack: dict[str, bool]) -> list[str]:
    """Pop nodes from stack until v is reached, forming one SCC."""
    scc: list[str] = []
    while True:
        w = stack.pop()
        on_stack[w] = False
        scc.append(w)
        if w == v:
            break
    return scc


def _tarjan_step(
    v: str,
    i: int,
    adj: dict[str, list[str]],
    index: dict[str, int],
    lowlink: dict[str, int],
    on_stack: dict[str, bool],
    stack: list[str],
    work: list[tuple[str, int]],
    sccs: list[list[str]],
    counter: list[int],
) -> None:
    """Process one step of the iterative Tarjan walk: advance or backtrack."""
    neighbors = adj.get(v, [])
    if i < len(neighbors):
        work[-1] = (v, i + 1)
        w = neighbors[i]
        if w not in index:
            index[w] = counter[0]
            lowlink[w] = counter[0]
            counter[0] += 1
            stack.append(w)
            on_stack[w] = True
            work.append((w, 0))
        elif on_stack.get(w, False):
            lowlink[v] = min(lowlink[v], index[w])
    else:
        work.pop()
        if work:
            lowlink[work[-1][0]] = min(lowlink[work[-1][0]], lowlink[v])
        if lowlink[v] == index[v]:
            sccs.append(_pop_scc(v, stack, on_stack))


def tarjan_scc(adj: dict[str, list[str]]) -> list[list[str]]:
    """Iterative Tarjan's SCC algorithm.

    Returns all strongly connected components. O(V+E), no recursion limit.
    Any returned component with len > 1 is a cycle.
    """
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = [0]

    for start in sorted(adj):
        if start in index:
            continue
        index[start] = counter[0]
        lowlink[start] = counter[0]
        counter[0] += 1
        stack.append(start)
        on_stack[start] = True
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            v, i = work[-1]
            _tarjan_step(v, i, adj, index, lowlink, on_stack, stack, work, sccs, counter)

    return sccs
