"""FastAPI route handlers: the `route` role a function carries (#566).

A handler stays a FUNCTION or METHOD — every index, impact walk and orphan
query reads it as one — and gains `metadata["route"] = {"verb", "router"}`,
which `cgis audit --from-type ROUTE_HANDLER` selects on. Read only for a
router this module builds (`router = APIRouter(...)`, `app = FastAPI()`): a
decorator merely shaped like `<name>.get` (a cache, a dict) is no route, and an
imported name could be anything.
"""

from tree_sitter import Node as BaseNode

from cgis.core.models import Node, NodeType

#: The classes whose instances route: an app and a router.
_ROUTER_CLASSES = frozenset({"fastapi.FastAPI", "fastapi.APIRouter", "fastapi.routing.APIRouter"})
#: The decorator attributes that register a handler.
_VERBS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace", "api_route", "websocket"}
)


def _text(node: BaseNode | None, code: bytes) -> str:
    return code[node.start_byte : node.end_byte].decode("utf8") if node is not None else ""


def _constructed_class(value: BaseNode | None, code: bytes, import_map: dict[str, str]) -> str:
    """The dotted class a `Class(...)` call builds, through the import map; '' otherwise."""
    if value is None or value.type != "call":
        return ""
    called = _text(value.child_by_field_name("function"), code)
    head, dot, rest = called.partition(".")
    resolved = import_map.get(head, head)
    return f"{resolved}{dot}{rest}"


def routers(root: BaseNode, code: bytes, import_map: dict[str, str]) -> set[str]:
    """Module-level names bound to a FastAPI app or router: `router = APIRouter()`."""
    names: set[str] = set()
    for statement in root.children:
        assignment = (
            statement.named_children[0] if statement.type == "expression_statement" else None
        )
        if assignment is None or assignment.type != "assignment":
            continue
        target = assignment.child_by_field_name("left")
        value = assignment.child_by_field_name("right")
        if (
            target is not None
            and target.type == "identifier"
            and _constructed_class(value, code, import_map) in _ROUTER_CLASSES
        ):
            names.add(_text(target, code))
    return names


def _route(decorators: list[str], router_names: set[str]) -> dict[str, str] | None:
    for decorator in decorators:
        router, _, verb = decorator.rpartition(".")
        if router in router_names and verb in _VERBS:
            return {"verb": verb, "router": router}
    return None


def mark_routes(nodes: list[Node], router_names: set[str]) -> list[Node]:
    """`nodes` with every handler decorated by one of `router_names` carrying its route."""
    if not router_names:
        return nodes
    return [_with_route(node, router_names) for node in nodes]


def _with_route(node: Node, router_names: set[str]) -> Node:
    if node.type not in (NodeType.FUNCTION, NodeType.METHOD):
        return node
    route = _route(node.metadata.get("decorators") or [], router_names)
    if route is None:
        return node
    return node.model_copy(update={"metadata": {**node.metadata, "route": route}})
