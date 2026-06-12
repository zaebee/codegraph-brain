"""Serialize a (nodes, edges) subgraph into a machine-readable JSON payload.

Mermaid output (``mermaid.py``) is for human eyes; this is the joinable,
agent/CI-facing view of the same subgraph. FQNs are emitted verbatim — no
display hashes — so results from separate queries can be combined with plain
set operations (e.g. authz-coverage or dead-code sweeps). See issue #171.
"""

from typing import Any

from cgis.core.models import Edge, Node


def graph_to_json(root: str, nodes: list[Node], edges: list[Edge]) -> dict[str, Any]:
    """Build the JSON shape ``{root, nodes, edges}`` for a traversal result.

    ``root`` is the resolved FQN the traversal started from. Node entries carry
    ``fqn``/``type``/``file``/``line``; edge entries carry ``src``/``dst``/
    ``type``/``confidence``. Unresolved targets keep their ``raw_call:`` prefix
    so consumers can tell a resolved edge from a dangling one.
    """
    return {
        "root": root,
        "nodes": [
            {
                "fqn": node.id,
                "type": node.type.value,
                "file": node.file_path,
                "line": node.start_line,
            }
            for node in nodes
        ],
        "edges": [
            {
                "src": edge.source,
                "dst": edge.target,
                "type": edge.type.value,
                "confidence": edge.confidence,
            }
            for edge in edges
        ],
    }
