"""Shared fixtures for self-parsing tests."""

from collections.abc import Generator
from pathlib import Path

import pytest

from cgis.core.models import Edge, Node
from cgis.extractors.python_extractor import PythonExtractor
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

SRC_DIR = Path(__file__).parent.parent.parent / "src" / "cgis"
TS_SRC = Path(__file__).parent.parent.parent / "ui" / "src"

skip_if_no_ui = pytest.mark.skipif(
    not TS_SRC.exists(),
    reason="ui/src/ not available (requires feat/ui merge)",
)


@pytest.fixture(scope="session")
def graph_data(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[tuple[SQLiteStore, list[Node], list[Edge]], None, None]:
    """Run the full pipeline once on src/cgis/ and share across all self-parsing tests."""
    db_path = str(tmp_path_factory.mktemp("self_parse") / "graph.db")
    pipeline = IngestionPipeline({".py": PythonExtractor()})
    with SQLiteStore(db_path) as store:
        nodes, _, resolved_edges = pipeline.run(str(SRC_DIR), store=store)
        yield store, nodes, resolved_edges


@pytest.fixture(scope="session")
def ts_graph_data(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[tuple[SQLiteStore, list[Node], list[Edge]], None, None]:
    """Run the full pipeline on ui/src/ and share across all TS self-parsing tests."""
    db_path = str(tmp_path_factory.mktemp("self_parse_ts") / "graph.db")
    pipeline = IngestionPipeline(
        {".ts": TypeScriptExtractor(), ".tsx": TypeScriptExtractor(tsx=True)}
    )
    with SQLiteStore(db_path) as store:
        nodes, _, resolved_edges = pipeline.run(str(TS_SRC), store=store)
        yield store, nodes, resolved_edges
