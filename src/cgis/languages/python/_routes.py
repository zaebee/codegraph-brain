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


#: Module-level compound statements whose bodies still bind module names.
_MODULE_BLOCKS = frozenset(
    {
        "if_statement",
        "elif_clause",
        "else_clause",
        "try_statement",
        "except_clause",
        "finally_clause",
        "with_statement",
        "block",
    }
)


def routers(root: BaseNode, code: bytes, import_map: dict[str, str]) -> set[str]:
    """Module-level names bound to a FastAPI app or router: `router = APIRouter()`.

    Through module-level `if`/`try`/`with` blocks, which still bind module names,
    and tuple or chained assignment; never into a function or class body.
    """
    names: set[str] = set()
    for statement in _module_statements(root):
        for assignment in (c for c in statement.named_children if c.type == "assignment"):
            names |= _bound_routers(assignment, code, import_map)
    return names


def _module_statements(node: BaseNode) -> list[BaseNode]:
    """Every statement that runs at module level, nested blocks flattened."""
    statements: list[BaseNode] = []
    for child in node.named_children:
        if child.type in _MODULE_BLOCKS:
            statements += _module_statements(child)
        elif child.type == "expression_statement":
            statements.append(child)
    return statements


def _bound_routers(assignment: BaseNode, code: bytes, import_map: dict[str, str]) -> set[str]:
    """The names an assignment binds to a router: `a = R()`, `a, b = R(), R()`, `a = b = R()`."""
    left = assignment.child_by_field_name("left")
    right = assignment.child_by_field_name("right")
    if right is not None and right.type == "assignment":  # `a = b = R()`
        inner = _bound_routers(right, code, import_map)
        target = _text(left, code)
        return (
            inner | {target} if inner and left is not None and left.type == "identifier" else inner
        )
    if left is None or right is None:
        return set()
    if left.type == "identifier":
        built = _constructed_class(right, code, import_map) in _ROUTER_CLASSES
        return {_text(left, code)} if built else set()
    if left.type in ("pattern_list", "tuple_pattern") and right.type in (
        "expression_list",
        "tuple",
    ):
        pairs = zip(left.named_children, right.named_children, strict=False)
        return {
            _text(name, code)
            for name, value in pairs
            if name.type == "identifier"
            and _constructed_class(value, code, import_map) in _ROUTER_CLASSES
        }
    return set()


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
