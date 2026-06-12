"""End-to-end acceptance for #161 slice 2 — symbol-level import edges.

Distilled repro of the issue's gap 3: a module that IMPORTS a symbol but
never calls it must appear in the symbol's impact set.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from cgis.core.models import EdgeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.engine import QueryEngine
from cgis.storage.sqlite_store import SQLiteStore

_DEFS_PY = """\
class Router:
    \"\"\"A routing table.\"\"\"

    def add(self) -> None:
        \"\"\"Register a route.\"\"\"
"""

_CONSUMER_PY = """\
from defs import Router

__all__ = ["Router"]
"""


@pytest.fixture
def import_graph_store(tmp_path: Path) -> Iterator[SQLiteStore]:
    """Ingest the #161 slice-2 fixture into a real SQLiteStore and yield it (open)."""
    (tmp_path / "defs.py").write_text(_DEFS_PY, encoding="utf-8")
    (tmp_path / "consumer.py").write_text(_CONSUMER_PY, encoding="utf-8")

    pipeline = IngestionPipeline({".py": PythonExtractor()})
    nodes, _raw, resolved = pipeline.run(str(tmp_path))

    with SQLiteStore(str(tmp_path / "graph.db")) as store:
        store.save_graph(nodes, resolved, overwrite=True)
        yield store


def _find_id(store: SQLiteStore, suffix: str) -> str:
    """Find the unique node id ending with `.suffix` (dot boundary)."""
    matches = store.find_nodes_by_suffix(suffix, limit=2)
    assert len(matches) == 1, f"expected unique node for {suffix!r}, got {matches}"
    return matches[0].id


def test_symbol_import_surfaces_importer_in_impact(import_graph_store: SQLiteStore) -> None:
    """Impact traversal from Router includes consumer module via IMPORTS_SYMBOL edge."""
    engine = QueryEngine(import_graph_store)
    router_id = _find_id(import_graph_store, "Router")

    imp_nodes, imp_edges = engine.get_impact_graph(router_id, max_depth=2)
    impact_ids = {n.id for n in imp_nodes}

    # consumer module must appear in the impact set
    consumer_matches = [nid for nid in impact_ids if nid == "consumer" or nid.endswith(".consumer")]
    assert consumer_matches, (
        f"consumer module missing from impact of Router.\nImpact node ids: {sorted(impact_ids)}"
    )
    consumer_id = consumer_matches[0]

    # the connecting edge must be IMPORTS_SYMBOL with confidence 1.0
    connecting = [e for e in imp_edges if e.source == consumer_id and e.target == router_id]
    assert connecting, (
        f"No IMPORTS_SYMBOL edge from consumer to Router found in impact edges.\n"
        f"Impact edges: {[(e.source, e.target, e.type) for e in imp_edges]}"
    )
    edge = connecting[0]
    assert edge.type == EdgeType.IMPORTS_SYMBOL, f"Expected IMPORTS_SYMBOL, got {edge.type}"
    assert edge.confidence == pytest.approx(1.0), f"Expected confidence 1.0, got {edge.confidence}"


def test_no_raw_import_in_stored_graph(import_graph_store: SQLiteStore) -> None:
    """No edge in the stored graph must have a target starting with 'raw_import:'."""
    all_edges = import_graph_store.get_all_edges()
    leaked = [e for e in all_edges if e.target.startswith("raw_import:")]
    assert leaked == [], (
        f"raw_import: targets leaked into stored graph: {[(e.source, e.target) for e in leaked]}"
    )
