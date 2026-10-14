"""A traversal as an indented tree, one line per node — the compact form for agents (#569).

JSON repeats every FQN in a node entry and again in each edge, at about 400
characters a node: `cgis_trace_flow(cgis.cli.ingest, depth=3)` was 29k
characters for 70 nodes, and the agent that received it named every node in it
(#580). The same answer as a tree is 6k. Indentation is the depth and carries
the parent, so no edge needs its own line; a node's children come in the order
of the lines that call them, so a flow reads top to bottom.

A node reached again from a second parent is not expanded twice: a call gets a
`↪` line pointing back at it, and a repeated non-call edge — an annotation
naming `Node` in every signature — is left out, since it says nothing about
what runs. JSON keeps the full edge list for anything that needs it.
"""

from collections import defaultdict

from cgis.core.models import Edge, EdgeType, Node

#: Edges that name a symbol without running it. A node first met through one is
#: not expanded there when a call reaches it too: a type annotation listed early
#: would otherwise take the subtree, and the call site that runs it would get only
#: a `↪` (Gemini on #581).
_MENTIONS = frozenset({EdgeType.REFERENCES, EdgeType.IMPORTS, EdgeType.IMPORTS_SYMBOL})

#: Containment is what a structure answer is made of; tagging every line says nothing.
_UNTAGGED = frozenset({EdgeType.CALLS, EdgeType.CONTAINS, EdgeType.DECLARES})

_Children = dict[str, set[tuple[int, str, EdgeType]]]


def graph_to_text(root: str, nodes: list[Node], edges: list[Edge], *, downstream: bool) -> str:
    """The subgraph rooted at `root` as an indented tree.

    ``downstream`` walks edges source → target (trace, structure); otherwise
    target → source (impact), so a node's children are its callers. A node is
    `FQN  file:line`, with the edge type in brackets when it is neither a call
    nor containment. Children come in order of the line of the edge: the call
    site downstream; upstream, lines from different callers' files, so only
    a stable order there.
    """
    by_id = {node.id: node for node in nodes}
    children = _children(edges, by_id, downstream)
    run = _reached_without_mentions(root, children)
    lines: list[str] = []
    seen: set[str] = set()
    stack: list[tuple[str, int, EdgeType | None]] = [(root, 0, None)]
    while stack:
        node_id, depth, via = stack.pop()
        line = _line(node_id, depth, via, by_id, seen, run)
        if line is None:
            continue
        lines.append(line)
        if node_id in seen:
            continue
        seen.add(node_id)
        # Reversed onto the stack, so the earliest call site is expanded first.
        stack.extend(
            (child, depth + 1, edge_type)
            for _line_no, child, edge_type in sorted(children[node_id], reverse=True)
        )
    return "\n".join([*lines, *_unreached(by_id, seen, root)])


def _children(edges: list[Edge], by_id: dict[str, Node], downstream: bool) -> _Children:
    """Each node's children as (call-site line, child, edge type), in the walk's direction.

    One entry per (parent, child, type), at its first call site: a callee called on
    three lines of one function printed three `↪` lines (#581 review).
    """
    first: dict[tuple[str, str, EdgeType], int] = {}
    for edge in edges:
        parent, child = (edge.source, edge.target) if downstream else (edge.target, edge.source)
        if child in by_id:
            key = (parent, child, edge.type)
            line = edge.line_number or 0
            first[key] = min(line, first.get(key, line))
    children: _Children = defaultdict(set)
    for (parent, child, edge_type), line in first.items():
        children[parent].add((line, child, edge_type))
    return children


def _unreached(by_id: dict[str, Node], seen: set[str], root: str) -> list[str]:
    """Nodes the tree never reached from the root, listed so none is dropped unsaid."""
    rest = sorted(set(by_id) - seen - {root})
    return ["(not reached from the root:)", *(f"  {n}" for n in rest)] if rest else []


def _reached_without_mentions(root: str, children: _Children) -> set[str]:
    """The nodes the root reaches through edges that run code, not only name it."""
    reached = {root}
    stack = [root]
    while stack:
        current = stack.pop()
        for _line_no, child, edge_type in children[current]:
            if edge_type not in _MENTIONS and child not in reached:
                reached.add(child)
                stack.append(child)
    return reached


def _line(
    node_id: str,
    depth: int,
    via: EdgeType | None,
    by_id: dict[str, Node],
    seen: set[str],
    run: set[str],
) -> str | None:
    """The line for one visit of a node, or None when this visit prints nothing.

    A repeat prints a `↪` only when it is a call; a mention of a node some call
    also reaches prints nothing here, the call site expands it instead.
    """
    is_call = via is None or via not in _MENTIONS
    tag = "" if via is None or via in _UNTAGGED else f" [{via.value}]"
    indent = "  " * depth
    if node_id in seen:
        return f"{indent}↪ {node_id}{tag}" if is_call else None
    if not is_call and node_id in run:
        return None
    node = by_id.get(node_id)
    # A synthesized package row (#487) has a directory and no line.
    line = f":{node.start_line}" if node and node.start_line else ""
    where = f"  {node.file_path}{line}" if node else ""
    return f"{indent}{node_id}{where}{tag}"
