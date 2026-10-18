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
    files: dict[str, Node] = {}  # FILE nodes by path, filled at the first `@NAME`
    for edge in edges:
        if edge.target.startswith(RAW_KEY_PREFIX):
            keys.append(_resolved_key_edge(edge, index, files))
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


def _resolved_key_edge(edge: Edge, index: SymbolIndex, files: dict[str, Node]) -> Edge:
    """Point a raw_key: edge at its key id, resolving an `@NAME` namespace.

    The namespace is the string the module-level constant NAME holds, recorded in a
    FILE node's `str_constants`: the edge's own file's, else the file its import of
    NAME comes from. A name that resolves to no single string leaves the key
    unscoped, as a namespace living in another function does.
    """
    namespace, _, path = edge.target.removeprefix(RAW_KEY_PREFIX).partition("|")
    if namespace.startswith("@"):
        source_file = index.normalized_file_path(edge.source, edge.file_path)
        if not files:
            files.update(
                (os.path.normpath(n.file_path), n)
                for n in index.nodes.values()
                if n.type is NodeType.FILE
            )
        namespace = _constant_value(namespace[1:], source_file, index, files)
    return edge.model_copy(update={"target": key_id(namespace, path)})


def _constant_value(
    name: str, source_file: str | None, index: SymbolIndex, files: dict[str, Node]
) -> str:
    """What constant `name` holds where `source_file` uses it; "" when not one string."""
    if source_file is None:
        return ""
    own = _constants(files.get(source_file)).get(name)
    if own is not None:
        return own
    target = index.file_imports.get(source_file, {}).get(name)
    if target is None or "." not in target:
        return ""
    module, _, imported = target.rpartition(".")
    return _constants(_module_file(module, index)).get(imported, "")


def _module_file(fqn: str, index: SymbolIndex) -> Node | None:
    """The FILE node of module `fqn`, through the suffix map for a src/ layout."""
    node = index.nodes.get(fqn)
    if node is not None and node.type is NodeType.FILE:
        return node
    found = [index.nodes[c] for c in index.suffix_map.get(fqn, [])]
    found = [f for f in found if f.type is NodeType.FILE]
    return found[0] if len(found) == 1 else None


def _constants(file: Node | None) -> dict[str, str]:
    """A FILE node's module-level string constants; none for a missing file."""
    constants = file.metadata.get("str_constants") if file is not None else None
    return constants if isinstance(constants, dict) else {}
