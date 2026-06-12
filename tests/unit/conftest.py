"""Shared test helpers for the tests/unit suite.

Plain importable functions (no pytest fixtures) so any test module can do::

    from conftest import make_chain_db, make_chain_nodes_edges
"""

from pathlib import Path

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.storage.sqlite_store import SQLiteStore


def make_chain_nodes_edges(prefix: str, count: int) -> tuple[list[Node], list[Edge]]:
    """Return ``count`` FUNCTION nodes and a CALLS chain wired as f0→f1→…→fn-1.

    Args:
        prefix: FQN prefix for all nodes (e.g. ``"app.pipeline"``).
        count:  Number of nodes in the chain.

    Returns:
        A ``(nodes, edges)`` tuple ready for ``SQLiteStore.save_graph``.
    """
    nodes: list[Node] = [
        Node(
            id=f"{prefix}.f{i}",
            type=NodeType.FUNCTION,
            name=f"f{i}",
            file_path=f"{prefix.replace('.', '/')}.py",
            start_line=1,
            end_line=2,
        )
        for i in range(count)
    ]
    edges: list[Edge] = [
        Edge(
            id=f"e{prefix}{i}",
            source=f"{prefix}.f{i}",
            target=f"{prefix}.f{i + 1}",
            type=EdgeType.CALLS,
        )
        for i in range(count - 1)
    ]
    return nodes, edges


def make_chain_db(tmp_path: Path, prefix: str = "app.chain", count: int = 12) -> str:
    """Build a SQLite graph database with ``count`` functions wired as a CALLS chain.

    Args:
        tmp_path: Temporary directory (pass ``tmp_path`` from the calling test).
        prefix:   FQN prefix for all nodes (default ``"app.chain"``).
        count:    Number of nodes in the chain (default 12).

    Returns:
        Absolute path to the SQLite file as a string.
    """
    db = str(tmp_path / "chain.db")
    nodes, edges = make_chain_nodes_edges(prefix, count)
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db
