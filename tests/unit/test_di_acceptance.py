"""End-to-end acceptance for FastAPI DI modeling (#161 slice 1).

Replicates the exact reproducible case from issue #161: two providers, two
Annotated DI aliases, four consuming endpoints. Asserts via QueryEngine that
impact and flow traversals surface the DI coupling layer.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.engine import QueryEngine
from cgis.storage.sqlite_store import SQLiteStore

_DEPS_PY = """
from typing import Annotated
from fastapi import Depends, Path


class User:
    pass


def get_owner_service():
    pass


OwnerServiceDep = Annotated[object, Depends(get_owner_service)]


async def resolve_owner(owner_service: OwnerServiceDep, owner_id: str = Path(...)) -> User:
    pass


ResolvedOwnerDep = Annotated[User, Depends(resolve_owner)]


async def resolve_published_owner(owner: ResolvedOwnerDep) -> User:
    pass


PublishedOwnerDep = Annotated[User, Depends(resolve_published_owner)]
"""

_ROUTES_PY = """
from deps import PublishedOwnerDep


async def get_owner_public_profile(owner: PublishedOwnerDep):
    pass


async def search_vehicles(owner: PublishedOwnerDep):
    pass


async def get_vehicle(owner: PublishedOwnerDep):
    pass


async def calculate_pricing(owner: PublishedOwnerDep):
    pass
"""

_ENDPOINTS = (
    "get_owner_public_profile",
    "search_vehicles",
    "get_vehicle",
    "calculate_pricing",
)


@pytest.fixture
def di_graph_store(tmp_path: Path) -> Iterator[SQLiteStore]:
    """Ingest the #161 fixture into a real SQLiteStore and yield it (open)."""
    (tmp_path / "deps.py").write_text(_DEPS_PY, encoding="utf-8")
    (tmp_path / "routes.py").write_text(_ROUTES_PY, encoding="utf-8")

    pipeline = IngestionPipeline({".py": PythonExtractor()})
    nodes, _raw, resolved = pipeline.run(str(tmp_path))

    with SQLiteStore(str(tmp_path / "graph.db")) as store:
        store.save_graph(nodes, resolved, overwrite=True)
        yield store


def _find_id(store: SQLiteStore, suffix: str) -> str:
    """Find the unique node id ending with `.suffix` (dot boundary)."""
    matches = store.find_nodes_by_suffix(suffix, limit=2)
    assert len(matches) == 1, f"expected unique node for {suffix}, got {matches}"
    return matches[0].id


def test_impact_surfaces_alias_and_all_endpoints(di_graph_store: SQLiteStore) -> None:
    """Impact traversal from resolve_published_owner surfaces alias and all endpoints."""
    engine = QueryEngine(di_graph_store)
    rpo = _find_id(di_graph_store, "resolve_published_owner")

    imp_nodes, _ = engine.get_impact_graph(rpo, max_depth=4)
    ids = {n.id for n in imp_nodes}

    assert any(i.endswith(".PublishedOwnerDep") for i in ids)
    for endpoint in _ENDPOINTS:
        assert any(i.endswith(f".{endpoint}") for i in ids), f"missing endpoint {endpoint}"


def test_flow_includes_sub_dependency_chain(di_graph_store: SQLiteStore) -> None:
    """Flow traversal from resolve_published_owner includes sub-dependency chain."""
    engine = QueryEngine(di_graph_store)
    rpo = _find_id(di_graph_store, "resolve_published_owner")

    flow_nodes, _ = engine.get_flow_graph(rpo, max_depth=3)
    ids = {n.id for n in flow_nodes}

    assert any(i.endswith(".ResolvedOwnerDep") for i in ids)
    assert any(i.endswith(".resolve_owner") for i in ids)
