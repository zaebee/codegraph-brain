"""FastAPI route handlers carry a `route` role, so `cgis audit --from-type ROUTE_HANDLER`
audits them (#566). The node keeps its structural type: a handler is still a function,
which every index and query reads as one.
"""

from pathlib import Path

from cgis.core.models import Node, NodeType
from cgis.languages import extractors
from cgis.languages.python.extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.context.audit import audit_reachability
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
