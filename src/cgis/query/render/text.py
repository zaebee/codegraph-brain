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


def graph_to_text(root: str, nodes: list[Node], edges: list[Edge], *, downstream: bool) -> str:
    """The subgraph rooted at `root` as an indented tree.

    ``downstream`` walks edges source → target (trace, structure); otherwise
    target → source (impact), so a node's children are its callers. A node is
    `FQN  file:line`, with the edge type in brackets when it is not a call.
    """
    by_id = {node.id: node for node in nodes}
    children: dict[str, set[tuple[int, str, EdgeType]]] = defaultdict(set)
    for edge in edges:
        parent, child = (edge.source, edge.target) if downstream else (edge.target, edge.source)
        if child in by_id:
            children[parent].add((edge.line_number or 0, child, edge.type))
    lines: list[str] = []
    seen: set[str] = set()
    stack: list[tuple[str, int, EdgeType | None]] = [(root, 0, None)]
    while stack:
        node_id, depth, via = stack.pop()
        tag = "" if via is None or via is EdgeType.CALLS else f" [{via.value}]"
        indent = "  " * depth
        if node_id in seen:
            if via in (None, EdgeType.CALLS):
                lines.append(f"{indent}↪ {node_id}{tag}")
            continue
        seen.add(node_id)
        node = by_id.get(node_id)
        where = f"  {node.file_path}:{node.start_line}" if node else ""
        lines.append(f"{indent}{node_id}{where}{tag}")
        # Reversed onto the stack, so the earliest call site is expanded first.
        for _line, child, edge_type in sorted(children[node_id], reverse=True):
            stack.append((child, depth + 1, edge_type))
    return "\n".join(lines)
