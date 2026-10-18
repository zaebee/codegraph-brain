"""Function values in Python: which parameters a function calls, and which values it hands over.

The clauses are docs/specs/core/ontology.yml CV-1 and CV-4 (#632). A function that
calls its own parameter records that parameter's slots as `metadata["invokes"]`;
every argument naming a function value becomes a `value-arg:` candidate, which the
resolver keeps only when the call resolves to an invoker of that slot and the value
to a real function or method. The walk reads one function's own body: a nested
`def`, `lambda` or class is a body of its own.
"""

from collections.abc import Callable, Iterator

from tree_sitter import Node as BaseNode

from cgis.core.models import RAW_CALL_PREFIX, VALUE_ARG_CONTEXT, Edge, EdgeType, Node, NodeType
from cgis.languages.python._syntax import get_id, get_identifier

#: Nodes whose body is not the enclosing function's.
_OWN_SCOPES = frozenset(
    {"function_definition", "lambda", "class_definition", "decorated_definition"}
)
#: Parameter shapes with a plain name: the name is the node itself or its first identifier.
_NAMED_PARAMETERS = frozenset(
    {"identifier", "typed_parameter", "default_parameter", "typed_default_parameter"}
)
_PARTIAL = frozenset({"partial", "functools.partial"})
_CALLABLE = frozenset({NodeType.FUNCTION, NodeType.METHOD})

#: The values a local holds at a call, for CV-2 (None: no local holds any).
Held = Callable[[BaseNode], list[str]]


def _own_nodes(func: BaseNode) -> Iterator[BaseNode]:
    """Every node of `func`'s body in source order, nested scopes left out."""
    body = func.child_by_field_name("body")
    stack = [body] if body is not None else []
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed([c for c in node.children if c.type not in _OWN_SCOPES]))


def _parameter_name(param: BaseNode, code: bytes) -> str | None:
    """The name a plain parameter binds, or None for a splat or separator."""
    if param.type == "identifier":
        return get_identifier(param, code)
    if param.type in ("default_parameter", "typed_default_parameter"):
        name = param.child_by_field_name("name")
        return get_identifier(name, code) if name is not None else None
    first = param.named_children[0] if param.named_children else None
    return get_identifier(first, code) if first is not None and first.type == "identifier" else None


def _parameters(
    func: BaseNode, code: bytes, skip_first: bool
) -> list[tuple[str, int | None, bool]]:
    """(name, position or None, callable by keyword) for each plain parameter."""
    node = func.child_by_field_name("parameters")
    params = [c for c in node.named_children if c.type != "comment"] if node is not None else []
    found: list[tuple[str, int | None, bool]] = []
    position: int | None = 0
    for param in params[1:] if skip_first else params:
        if param.type == "positional_separator":  # `/`: what came before has no name
            found = [(name, pos, False) for name, pos, _ in found]
            continue
        if param.type in ("list_splat_pattern", "keyword_separator"):
            position = None  # what follows is keyword-only
            continue
        name = _parameter_name(param, code) if param.type in _NAMED_PARAMETERS else None
        if name is None:
            continue
        found.append((name, position, True))
        if position is not None:
            position += 1
    return found


def _first_events(func: BaseNode, code: bytes) -> tuple[dict[str, int], dict[str, int]]:
    """The first call by bare name, and the first binding, of each name in `func`'s body."""
    calls: dict[str, int] = {}
    binds: dict[str, int] = {}
    for node in _own_nodes(func):
        if node.type == "call":
            callee = node.child_by_field_name("function")
            if callee is not None and callee.type == "identifier":
                calls.setdefault(get_identifier(callee, code), node.start_byte)
        for target in _bound_names(node):
            binds.setdefault(get_identifier(target, code), target.start_byte)
    return calls, binds


def _bound_names(node: BaseNode) -> list[BaseNode]:
    """The identifiers `node` binds: an assignment's or a loop's target, `as`, walrus."""
    if node.type in ("assignment", "augmented_assignment", "for_statement"):
        target = node.child_by_field_name("left")
    elif node.type == "named_expression":
        target = node.child_by_field_name("name")
    elif node.type in ("as_pattern_target",):
        target = node
    else:
        return []
    if target is None:
        return []
    if target.type == "identifier":
        return [target]
    return [c for c in target.named_children if c.type == "identifier"]


def invoked_slots(func: BaseNode, code: bytes, skip_first: bool) -> list[int | str]:
    """The slots of each own parameter `func` calls (CV-4): its position, then its name.

    `skip_first` drops a method's receiver (`self`, `cls`); a staticmethod passes False.
    A parameter bound in the body before its first call holds something else there.
    """
    calls, binds = _first_events(func, code)
    slots: list[int | str] = []
    for name, position, by_keyword in _parameters(func, code, skip_first):
        called = calls.get(name)
        if called is None or binds.get(name, called) < called:
            continue
        if position is not None:
            slots.append(position)
        if by_keyword:
            slots.append(name)
    return slots


def local_names(func: BaseNode, code: bytes) -> frozenset[str]:
    """The names `func` binds itself: its parameters and every name its body assigns."""
    params = {name for name, _, _ in _parameters(func, code, skip_first=False)}
    return frozenset(params | set(_first_events(func, code)[1]))


def value_name(
    expr: BaseNode, code: bytes, file_names: frozenset[str], shadowed: frozenset[str]
) -> str | None:
    """The raw name of the function value `expr` names, or None if it names none.

    `file_names` are the module-level definitions and imports of the file; a bare
    name counts only when it is one of them and no local of the function shadows it.
    """
    if expr.type == "identifier":
        name = get_identifier(expr, code)
        return name if name in file_names and name not in shadowed else None
    if expr.type == "attribute":
        dotted = _dotted(expr, code)
        if dotted is None:
            return None
        head = dotted.partition(".")[0]
        if head == "self" or (head in file_names and head not in shadowed):
            return dotted
        return None
    if expr.type == "call":
        callee = expr.child_by_field_name("function")
        args = expr.child_by_field_name("arguments")
        if callee is None or args is None or _dotted(callee, code) not in _PARTIAL:
            return None
        first = next((a for a in args.named_children if a.type != "comment"), None)
        if first is None or first.type in ("keyword_argument", "list_splat", "dictionary_splat"):
            return None
        return value_name(first, code, file_names, shadowed)
    return None


def _dotted(expr: BaseNode, code: bytes) -> str | None:
    """`a.b.c` for a chain of plain attributes on a name, else None."""
    parts: list[str] = []
    while expr.type == "attribute":
        attr = expr.child_by_field_name("attribute")
        obj = expr.child_by_field_name("object")
        if attr is None or obj is None:
            return None
        parts.append(get_identifier(attr, code))
        expr = obj
    if expr.type != "identifier":
        return None
    parts.append(get_identifier(expr, code))
    return ".".join(reversed(parts))


def _arguments(call: BaseNode) -> Iterator[tuple[int | str, BaseNode]]:
    """(slot, value) per argument: positions up to a `*xs` splat, then keywords only."""
    args = call.child_by_field_name("arguments")
    if args is None or args.type != "argument_list":
        return
    position: int | None = 0
    for arg in args.named_children:
        if arg.type == "comment":
            continue
        if arg.type == "keyword_argument":
            name = arg.child_by_field_name("name")
            value = arg.child_by_field_name("value")
            if name is not None and value is not None:
                yield name.text.decode() if name.text else "", value
            continue
        if arg.type in ("list_splat", "dictionary_splat"):
            position = None
            continue
        if position is not None:
            yield position, arg
            position += 1


def value_candidates(
    func: BaseNode,
    code: bytes,
    source_id: str,
    file_path: str,
    file_names: frozenset[str],
    held: Held | None = None,
) -> list[Edge]:
    """A `value-arg:` candidate per function value `func` hands to a call (CV-1, CV-4).

    A local holding values (CV-2, `held`) passes what it holds, not its own name.
    """
    shadowed = local_names(func, code)
    edges: list[Edge] = []
    for call in (n for n in _own_nodes(func) if n.type == "call"):
        callee = call.child_by_field_name("function")
        raw = get_identifier(callee, code) if callee is not None else "unknown"
        if raw == "unknown":
            continue
        site = f"{raw}@{call.start_point.row}:{call.start_point.column}"
        for slot, value in _arguments(call):
            names = held(value) if held is not None and value.type == "identifier" else []
            if not names:
                name = value_name(value, code, file_names, shadowed)
                names = [name] if name is not None else []
            for i, name in enumerate(names):
                suffix = f"_{i}" if len(names) > 1 else ""
                edges.append(
                    Edge(
                        id=f"{file_path}:value_{value.start_byte}_{value.end_byte}{suffix}",
                        source=source_id,
                        target=f"{RAW_CALL_PREFIX}{name}",
                        type=EdgeType.CALLS,
                        confidence=0.1,
                        context=f"{VALUE_ARG_CONTEXT}{slot}:{site}",
                        file_path=file_path,
                        line_number=value.start_point.row + 1,
                    )
                )
    return edges


def function_values(
    root: BaseNode,
    code: bytes,
    file_path: str,
    source_root: str | None,
    nodes: list[Node],
    file_names: frozenset[str],
) -> tuple[list[Node], list[Edge]]:
    """`nodes` with each function's `invokes` recorded, and every value candidate.

    Run after the walk, when the file's definitions and import map are complete.
    """
    index = {n.id: i for i, n in enumerate(nodes) if n.type in _CALLABLE}
    updated = list(nodes)
    edges: list[Edge] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type != "function_definition":
            continue
        position = index.get(get_id(node, code, file_path, source_root))
        if position is None:
            continue
        owner = updated[position]
        static = "staticmethod" in owner.metadata.get("decorators", ())
        slots = invoked_slots(node, code, skip_first=owner.type is NodeType.METHOD and not static)
        if slots:
            updated[position] = owner.model_copy(
                update={"metadata": {**owner.metadata, "invokes": slots}}
            )
        edges.extend(value_candidates(node, code, owner.id, file_path, file_names))
    return updated, edges
