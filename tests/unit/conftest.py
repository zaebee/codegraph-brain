"""Shared test helpers for the tests/unit suite.

Plain importable functions (no pytest fixtures) so any test module can do::

    from conftest import make_chain_db, make_chain_nodes_edges, module_with_funcs
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


def module_with_funcs(prefix: str, fname: str, n_funcs: int) -> list[Node]:
    """One MODULE node + n FUNCTION children, all sharing ``fname`` as file_path.

    Promoted to conftest to avoid Sonar duplication across test_fingerprint,
    test_cli, and test_ontology_init (spec §176/#170 task 3 Sonar lesson).

    Args:
        prefix:  FQN of the module node (e.g. ``"app.svc.a"``).
        fname:   Relative file path shared by the module and all its children.
        n_funcs: Number of FUNCTION children to create.

    Returns:
        A list: one MODULE node followed by ``n_funcs`` FUNCTION nodes.
    """
    mod = Node(
        id=prefix,
        type=NodeType.MODULE,
        name=prefix.rsplit(".", maxsplit=1)[-1],
        file_path=fname,
        start_line=1,
        end_line=99,
    )
    funcs = [
        Node(
            id=f"{prefix}.f{i}",
            type=NodeType.FUNCTION,
            name=f"f{i}",
            file_path=fname,
            start_line=i + 1,
            end_line=i + 2,
        )
        for i in range(n_funcs)
    ]
    return [mod, *funcs]


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


def fit_patterns_yaml() -> str:
    """A minimal v2 patterns.yaml (profile + two ideal templates) for fit tests (#177).

    Profile ``py`` weights only the CALLS layer; templates ``pure_utility``
    (021U in-star) and ``pipeline_stage`` (021C chain) give a clean two-way
    ranking. Shared across the drift/cli/mcp fit tests (Sonar-dedup, #211 lesson).
    """
    return (
        "version: '2.0.0'\n"
        "profiles:\n  py:\n    drift_weights: {hub_count: 0.5, star_count: 0.5}\n"
        "    layers: {imports: 0.0, calls: 1.0, gates: 0.0}\n    triad_weights: {}\n"
        "patterns:\n"
        "  pure_utility:\n    description: u\n    ideal:\n"
        "      imports: {'021U': 1.0}\n      calls: {'021U': 1.0}\n"
        "  pipeline_stage:\n    description: c\n    ideal:\n"
        "      imports: {'021C': 1.0}\n      calls: {'021C': 1.0}\n"
        "project_domains:\n"
        "  - name: dom\n    fqn_prefix: dom\n    expected_pattern: pure_utility\n"
        "    profile: py\n    drift_tolerance: 0.5\n"
    )


def _three_func_db(tmp_path: Path, name: str, edges: list[Edge]) -> str:
    """Three FUNCTION nodes f1/f2/f3 under prefix 'dom' wired by ``edges``."""
    db = str(tmp_path / name)
    nodes = [
        Node(
            id=f"dom.f{i}",
            type=NodeType.FUNCTION,
            name=f"f{i}",
            file_path="dom.py",
            start_line=i,
            end_line=i + 1,
        )
        for i in (1, 2, 3)
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def instar_db(tmp_path: Path) -> str:
    """Three functions forming one 021U in-star (f1→f3, f2→f3) under 'dom' (#177)."""
    return _three_func_db(
        tmp_path,
        "instar.db",
        [
            Edge(id="e1", source="dom.f1", target="dom.f3", type=EdgeType.CALLS),
            Edge(id="e2", source="dom.f2", target="dom.f3", type=EdgeType.CALLS),
        ],
    )


def triangle_db(tmp_path: Path) -> str:
    """A 030T transitive triangle (f1→f2, f2→f3, f1→f3) under 'dom' — no 021* fits (#177)."""
    return _three_func_db(
        tmp_path,
        "tri.db",
        [
            Edge(id="e1", source="dom.f1", target="dom.f2", type=EdgeType.CALLS),
            Edge(id="e2", source="dom.f2", target="dom.f3", type=EdgeType.CALLS),
            Edge(id="e3", source="dom.f1", target="dom.f3", type=EdgeType.CALLS),
        ],
    )
