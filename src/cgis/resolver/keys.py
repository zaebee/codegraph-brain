"""Data keys in the resolver: raw_key: edges to key ids, and the virtual nodes behind them."""

import os

from cgis.core.keys import key_id, parse_key_id
from cgis.core.models import (
    RAW_KEY_PREFIX,
    VIRTUAL_FILE_PATH,
    Edge,
    Node,
    NodeNamespace,
    NodeType,
)
from cgis.resolver.indices import SymbolIndex


def resolve_key_edges(edges: list[Edge], index: SymbolIndex) -> tuple[list[Edge], list[Edge]]:
    """Split off the raw_key: edges, resolved to their key ids; the rest come back as they were."""
    keys: list[Edge] = []
    others: list[Edge] = []
    for edge in edges:
        if edge.target.startswith(RAW_KEY_PREFIX):
            keys.append(_resolved_key_edge(edge, index))
        else:
            others.append(edge)
    return keys, others


def key_nodes(key_edges: list[Edge]) -> dict[str, Node]:
    """One virtual DATA_KEY node per key id the resolved edges target, shared by every file."""
    nodes: dict[str, Node] = {}
    for edge in key_edges:
        namespace, path = parse_key_id(edge.target)
        nodes[edge.target] = Node(
            id=edge.target,
            type=NodeType.DATA_KEY,
            name=path,
            file_path=VIRTUAL_FILE_PATH,
            start_line=0,
            end_line=0,
            namespace=NodeNamespace.INTERNAL,
            metadata={"namespace": namespace, "path": path},
        )
    return nodes


def _resolved_key_edge(edge: Edge, index: SymbolIndex) -> Edge:
    """Point a raw_key: edge at its key id, resolving an `@NAME` namespace.

    The namespace is the `str_value` of the module-level constant NAME (the edge's
    own file's, else the only one the graph has); a name that resolves to no single
    string leaves the key unscoped, as a namespace living in another function does.
    """
    namespace, _, path = edge.target.removeprefix(RAW_KEY_PREFIX).partition("|")
    if namespace.startswith("@"):
        namespace = _constant_value(namespace[1:], edge, index)
    return edge.model_copy(update={"target": key_id(namespace, path)})


def _constant_value(name: str, edge: Edge, index: SymbolIndex) -> str:
    """The string a module-level constant holds, or "" when it is not one string."""
    source_file = index.normalized_file_path(edge.source, edge.file_path)
    values: dict[str, set[str]] = {}
    for fqn in index.variable_symbols.get(name, []):
        node = index.nodes[fqn]
        value = node.metadata.get("str_value")
        if isinstance(value, str) and value:
            values.setdefault(os.path.normpath(node.file_path), set()).add(value)
    found = values.get(source_file) if source_file is not None else None
    if found is None:
        found = set().union(*values.values())
    return next(iter(found)) if len(found) == 1 else ""
