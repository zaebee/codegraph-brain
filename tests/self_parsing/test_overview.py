"""The overview measured against a real tree, not a flat fixture (#478, #486 review).

The unit fixture is flat, so every prefix it can produce is a module — which is
exactly the shape that always works. cgis's own `src/` has the shape that does
not: `cgis/query/` has no `__init__.py`, so no node bears that name, and
`cgis/guardian/` has one whose containment edges point at nothing. A promise
about "the next call" has to be checked here or it checks nothing.
"""

from cgis.core.models import Node
from cgis.query.analysis.overview import build_overview
from cgis.query.engine import QueryEngine
from cgis.query.render.metrics import DuckDBAnalyzer
from cgis.storage.sqlite_store import SQLiteStore


def _prefixes(store: SQLiteStore) -> list[str]:
    report = build_overview(store)
    rows = report["packages"] + report["test_packages"]
    assert rows, "cgis's own graph has packages"
    return [str(row["prefix"]) for row in rows]


def test_every_prefix_finds_symbols_under_it(
    root_graph_data: tuple[SQLiteStore, list[Node], list],
) -> None:
    """`cgis_find_symbol(fqn_prefix=…)` is the documented next call, so it must hold here.

    Walked the way an agent would: take a symbol the package really holds, search
    for it scoped to the prefix, and expect it back.
    """
    store, nodes, _edges = root_graph_data

    for prefix in _prefixes(store):
        under = [n for n in nodes if n.id.startswith(f"{prefix}.") and n.name]
        assert under, f"{prefix} names a package with no symbols under it"
        wanted = under[0]
        matches = store.search_nodes(wanted.name, kinds=(), fqn_prefix=prefix, limit=20)
        assert any(node.id == wanted.id for node in matches), f"{prefix}: {wanted.id} not found"


def test_every_prefix_scopes_a_metrics_run(
    root_graph_data: tuple[SQLiteStore, list, list],
) -> None:
    """`cgis_metrics(scope=[…])` is the other documented next call."""
    store, _nodes, _edges = root_graph_data

    for prefix in _prefixes(store):
        with DuckDBAnalyzer(store.db_path) as analyzer:
            coupling = analyzer.get_coupling_metrics(limit=3, scope=[prefix])
        assert coupling, f"{prefix} scopes a metrics run to nothing"
        assert all(metric.node_id.startswith(prefix) for metric in coupling)


def test_every_prefix_answers_a_structure_query(
    root_graph_data: tuple[SQLiteStore, list[Node], list],
) -> None:
    """The #478 acceptance line, made true by #487.

    Both hard shapes are present here: `cgis.query` has no `__init__.py` and so no
    node of its own, and `cgis.guardian` has one whose containment edges point at
    nothing. Each must still list the modules it holds.
    """
    store, _nodes, _edges = root_graph_data
    prefixes = _prefixes(store)
    assert any(store.get_node(prefix) is None for prefix in prefixes), (
        "expected a package with no node — cgis/query has no __init__.py"
    )

    for prefix in prefixes:
        nodes, edges = QueryEngine(store).get_structural_graph(prefix, max_depth=1)
        held = [node.id for node in nodes if node.id != prefix]
        assert held, f"{prefix} lists nothing"
        assert all(node_id.startswith(f"{prefix}.") for node_id in held)
        assert {edge.source for edge in edges} == {prefix}
