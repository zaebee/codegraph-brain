"""Shared test helpers for the tests/unit suite.

Mostly plain importable functions (no pytest fixtures) so any test module can
do::

    from conftest import make_chain_db, make_chain_nodes_edges, module_with_funcs

`sample_record` is the one actual pytest fixture here, auto-discovered by
pytest under its conftest name rather than imported explicitly.
"""

from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.guardian.martian import ReviewRecord
from cgis.storage.sqlite_store import SQLiteStore

#: The three reviewer-identity fields every `ReviewRecord` fixture needs (#375).
#: Spread with `**` into either shape — a constructor call or a raw-dict row —
#: so the same literal cannot drift between the two. It was copied verbatim at
#: six sites before #385; the value is arbitrary, its *sameness* is the point.
IDENTITY_FIELDS = {
    "review_fingerprint": "abc123abc123",
    "review_fingerprint_source": "measured",
    "finder_provider": "gemini",
}


@pytest.fixture
def sample_record() -> ReviewRecord:
    """One minimal, valid `ReviewRecord` — the shape every test fixture copies.

    Mirrors `tests/unit/test_guardian_martian_script.py`'s `_record(...)`
    helper with the four reviewer-identity fields added (#375 Task 5).
    """
    return ReviewRecord(
        url="https://github.com/o/r/pull/1",
        project="p",
        pr_slice="graph",
        base_sha="b",
        head_sha="h",
        had_graph=True,
        finder_model="m",
        skeptic_model=None,
        findings=[],
        prompt_tokens=1,
        completion_tokens=1,
        duration_s=1.0,
        parse_failed=False,
        guardian_sha="sha",
        reviewed_at="2026-08-12T00:00:00+00:00",
        **IDENTITY_FIELDS,
    )


def make_file_node(fqn: str, path: str | None = None) -> Node:
    """A FILE node for graph tests; ``path`` defaults to the fqn as a slash path.

    Shared by the cohesion / suggest-packages / CLI tests to avoid duplicating
    the same Node construction across modules (#242 Sonar dedup).
    """
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=fqn.rsplit(".", 1)[-1],
        file_path=path if path is not None else fqn.replace(".", "/") + ".py",
        start_line=0,
        end_line=0,
    )


def make_import_edge(src: str, tgt: str) -> Edge:
    """An IMPORTS edge for graph tests (#242 Sonar dedup)."""
    return Edge(
        id=f"{src}:IMPORTS:{tgt}",
        source=src,
        target=tgt,
        type=EdgeType.IMPORTS,
        weight=1.0,
        confidence=1.0,
    )


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
        "hygiene:\n  cycle_ratio: {max: 0.0}\n"
        "patterns:\n"
        "  pure_utility:\n    description: u\n    ideal:\n"
        "      imports: {'021U': 1.0}\n      calls: {'021U': 1.0}\n"
        "  pipeline_stage:\n    description: c\n    ideal:\n"
        "      imports: {'021C': 1.0}\n      calls: {'021C': 1.0}\n"
        "project_domains:\n"
        "  - name: dom\n    fqn_prefix: dom\n    expected_pattern: pure_utility\n"
        "    profile: py\n    drift_tolerance: 0.5\n"
    )


# Node ids for the shared three-function 'dom' fixtures (#177 fit tests).
_F1, _F2, _F3 = "dom.f1", "dom.f2", "dom.f3"


def _three_func_db(tmp_path: Path, name: str, edges: list[Edge]) -> str:
    """Three FUNCTION nodes f1/f2/f3 under prefix 'dom' wired by ``edges``."""
    db = str(tmp_path / name)
    nodes = [
        Node(
            id=fid,
            type=NodeType.FUNCTION,
            name=fid.rsplit(".", maxsplit=1)[-1],
            file_path="dom.py",
            start_line=i,
            end_line=i + 1,
        )
        for i, fid in enumerate((_F1, _F2, _F3), start=1)
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
            Edge(id="e1", source=_F1, target=_F3, type=EdgeType.CALLS),
            Edge(id="e2", source=_F2, target=_F3, type=EdgeType.CALLS),
        ],
    )


def triangle_db(tmp_path: Path) -> str:
    """A 030T transitive triangle (f1→f2, f2→f3, f1→f3) under 'dom' — no 021* fits (#177)."""
    return _three_func_db(
        tmp_path,
        "tri.db",
        [
            Edge(id="e1", source=_F1, target=_F2, type=EdgeType.CALLS),
            Edge(id="e2", source=_F2, target=_F3, type=EdgeType.CALLS),
            Edge(id="e3", source=_F1, target=_F3, type=EdgeType.CALLS),
        ],
    )
