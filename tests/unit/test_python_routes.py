"""FastAPI route handlers carry a `route` role, so `cgis audit --from-type ROUTE_HANDLER`
audits them (#566). The node keeps its structural type: a handler is still a function,
which every index and query reads as one.
"""

from pathlib import Path

import pytest

from cgis.core.models import Node, NodeType
from cgis.languages import extractors
from cgis.languages.python.extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.context.audit import NoAuditSourcesError, audit_reachability
from cgis.storage.sqlite_store import SQLiteStore

ROUTES = """\
import fastapi
from fastapi import APIRouter, FastAPI as App
from cache import Cache

router = APIRouter(prefix="/orders")
app: App = App()
legacy = fastapi.APIRouter()
cache = Cache()


@router.get("/{order_id}")
def get_order(order_id: int) -> dict:
    return helper(order_id)


@app.post("/orders")
async def create_order() -> None:
    pass


@legacy.api_route("/old", methods=["GET"])
def old() -> None:
    pass


@cache.get
def cached() -> None:
    pass


@app.middleware("http")
async def timing(request, call_next):
    return await call_next(request)


@router.on_event("startup")
def warm() -> None:
    pass


def helper(order_id: int) -> dict:
    return {}


def test_get_order() -> None:
    get_order(1)
"""


def _nodes(code: str = ROUTES, path: str = "app/orders/routes.py") -> dict[str, Node]:
    nodes, _ = PythonExtractor().parse(code, path)
    return {n.name: n for n in nodes if n.type in (NodeType.FUNCTION, NodeType.METHOD)}


def test_a_handler_on_a_router_or_an_app_carries_its_route() -> None:
    nodes = _nodes()
    assert nodes["get_order"].metadata["route"] == {"verb": "get", "router": "router"}
    assert nodes["create_order"].metadata["route"] == {"verb": "post", "router": "app"}
    assert nodes["old"].metadata["route"] == {"verb": "api_route", "router": "legacy"}


def test_a_handler_keeps_its_structural_type() -> None:
    assert _nodes()["get_order"].type is NodeType.FUNCTION


def test_a_lookalike_decorator_on_something_else_is_not_a_route() -> None:
    """`cache.get` has a verb's name, but `cache` is no FastAPI router."""
    nodes = _nodes()
    assert "route" not in nodes["cached"].metadata
    assert "route" not in nodes["timing"].metadata  # a real app, but no verb: middleware
    assert "route" not in nodes["warm"].metadata
    assert "route" not in nodes["helper"].metadata
    assert "route" not in nodes["test_get_order"].metadata


def test_a_router_imported_from_another_module_is_not_assumed() -> None:
    """Only a router this module builds is known to be one; an imported name could be anything."""
    code = "from .api import router\n\n@router.get('/x')\ndef x() -> None:\n    pass\n"
    assert "route" not in _nodes(code)["x"].metadata


GUARDED = """\
from fastapi import APIRouter

router = APIRouter()


def verify_owner(order_id: int) -> None:
    pass


@router.get("/{order_id}")
def get_order(order_id: int) -> None:
    verify_owner(order_id)


@router.delete("/{order_id}")
def delete_order(order_id: int) -> None:
    pass


def helper() -> None:
    pass


def test_delete_order() -> None:
    delete_order(1)
"""


def _ingest(tmp_path: Path) -> str:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "routes.py").write_text(GUARDED, encoding="utf-8")
    db = str(tmp_path / "g.db")
    with SQLiteStore(db) as store:
        IngestionPipeline(extractors([])).run(str(tmp_path), store=store)
    return db


def test_the_audit_selects_exactly_the_handlers_by_their_role(tmp_path: Path) -> None:
    """#566: `--from-type ROUTE_HANDLER` found nothing, since no extractor set the type."""
    with SQLiteStore(_ingest(tmp_path)) as store:
        result = audit_reachability(
            store, target_fqn="app.routes.verify_owner", from_type=NodeType.ROUTE_HANDLER
        )
    assert [r.fqn for r in result.covered] == ["app.routes.get_order"]
    assert [r.fqn for r in result.gaps] == ["app.routes.delete_order"]


def test_a_handler_s_ontology_class_is_route_handler(tmp_path: Path) -> None:
    with SQLiteStore(_ingest(tmp_path)) as store:
        handler = store.get_node("app.routes.get_order")
        plain = store.get_node("app.routes.helper")
    assert handler is not None
    assert plain is not None
    assert handler.ontology_class == "RouteHandler"
    assert plain.ontology_class == "Function"


def _routed(code: str) -> set[str]:
    return {name for name, node in _nodes(code).items() if "route" in node.metadata}


def test_a_router_bound_inside_module_level_blocks_still_counts() -> None:
    """#566 review: `if`/`try`/`with` at module level, tuple and chained assignment."""
    code = (
        "from fastapi import APIRouter\n"
        "if True:\n    a = APIRouter()\n"
        "try:\n    b = APIRouter()\nexcept Exception:\n    b = APIRouter()\n"
        "c, d = APIRouter(), APIRouter()\n"
        "e = f = APIRouter()\n"
        "@a.get('/a')\ndef ha() -> None: ...\n"
        "@b.get('/b')\ndef hb() -> None: ...\n"
        "@c.get('/c')\ndef hc() -> None: ...\n"
        "@d.get('/d')\ndef hd() -> None: ...\n"
        "@e.get('/e')\ndef he() -> None: ...\n"
        "@f.get('/f')\ndef hf() -> None: ...\n"
    )
    assert _routed(code) == {"ha", "hb", "hc", "hd", "he", "hf"}


def test_a_router_built_inside_a_function_is_not_a_module_router() -> None:
    code = (
        "from fastapi import APIRouter\n"
        "def setup():\n    r = APIRouter()\n    @r.get('/x')\n    def x() -> None: ...\n"
    )
    assert _routed(code) == set()


def test_a_method_handler_and_a_router_from_fastapi_routing() -> None:
    """#566 review M1: a METHOD handler, `fastapi.routing`, a route under another decorator."""
    code = (
        "from fastapi.routing import APIRouter\n"
        "import functools\n"
        "router = APIRouter()\n"
        "class Views:\n"
        "    @router.get('/v')\n"
        "    def view(self) -> None: ...\n"
        "@functools.cache\n"
        "@router.post('/p')\n"
        "def stacked() -> None: ...\n"
    )
    nodes = _nodes(code)
    assert nodes["view"].type is NodeType.METHOD
    assert nodes["view"].metadata["route"] == {"verb": "get", "router": "router"}
    assert nodes["stacked"].metadata["route"] == {"verb": "post", "router": "router"}


def test_an_empty_role_selection_says_what_the_role_detects(tmp_path: Path) -> None:
    """#566 review M3: "none of type ROUTE_HANDLER" hid that only same-module routers count."""
    db = str(tmp_path / "g.db")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "plain.py").write_text(
        "def f() -> None:\n    g()\n\n\ndef g() -> None:\n    pass\n", encoding="utf-8"
    )
    with SQLiteStore(db) as store:
        IngestionPipeline(extractors([])).run(str(tmp_path), store=store)
        with pytest.raises(NoAuditSourcesError, match="same module"):
            audit_reachability(store, target_fqn="app.plain.g", from_type=NodeType.ROUTE_HANDLER)
