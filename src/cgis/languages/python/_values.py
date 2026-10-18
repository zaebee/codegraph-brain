"""Function values in Python: which parameters a function calls, and which values it hands over.

The clauses are docs/specs/core/ontology.yml CV-1 and CV-4 (#632). A function that
calls its own parameter records that parameter's slots as `metadata["invokes"]`;
every argument naming a function value becomes a `value-arg:` candidate, which the
resolver keeps only when the call resolves to an invoker of that slot and the value
to a real function or method. The walk reads one function's own body: a nested
`def`, `lambda` or class is a body of its own.
"""

from collections.abc import Callable, Iterator

import tree_sitter_python as tspython
from tree_sitter import Language, Query, QueryCursor
from tree_sitter import Node as BaseNode

from cgis.core.models import (
    RAW_CALL_PREFIX,
    VALUE_ARG_CONTEXT,
    VALUE_LOCAL_CONTEXT,
    Edge,
    EdgeType,
    Node,
    NodeType,
)
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
#: Literals whose elements a `for` loop binds its variable to, one by one.
_LITERALS = frozenset({"tuple", "list", "parenthesized_expression", "set"})
#: A right-hand side that may put a function value in a local (CV-2).
_HOLDABLE = frozenset({"identifier", "attribute", "call", "expression_list", *_LITERALS})
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


def _may_hold(right: BaseNode | None, code: bytes) -> bool:
    """Whether a binding's right side may name a function value: a cheap pre-check."""
    right = _unwrap(right) if right is not None else None
    if right is None or right.type not in _HOLDABLE:
        return False
    if right.type != "call":
        return True
    callee = right.child_by_field_name("function")
    return callee is not None and _dotted(callee, code) in _PARTIAL


class _Body:
    """One function's own body, read once: every pass below shares this walk."""

    def __init__(self, func: BaseNode, code: bytes, events: list[BaseNode] | None = None) -> None:
        """Note `func`'s calls and bindings: from `events` (its own, in source order), or a walk."""
        #: The body's calls, in source order.
        self.calls: list[BaseNode] = []
        #: The first call by bare name, and the first binding, of each name.
        self.first_call: dict[str, int] = {}
        self.first_bind: dict[str, int] = {}
        #: Whether some binding may hold a function value (CV-2 has work to do).
        self.holds = False
        for node in _own_nodes(func) if events is None else events:
            if node.type == "call":
                self._note_call(node, code)
            elif node.type in ("assignment", "for_statement") and not self.holds:
                self.holds = _may_hold(node.child_by_field_name("right"), code)
            self._note_binds(node, code)

    def _note_call(self, call: BaseNode, code: bytes) -> None:
        """Keep the call, and when it calls a bare name, the first such call."""
        self.calls.append(call)
        callee = _callee(call)
        if callee is not None and callee.type == "identifier":
            self.first_call.setdefault(get_identifier(callee, code), call.start_byte)

    def _note_binds(self, node: BaseNode, code: bytes) -> None:
        """The first binding of each name `node` binds."""
        # A comprehension binds its variable before the expression written ahead of it.
        scope = node.parent if node.type == "for_in_clause" else None
        for target in _bound_names(node):
            at = scope.start_byte if scope is not None else target.start_byte
            self.first_bind.setdefault(get_identifier(target, code), at)


def _bound_names(node: BaseNode) -> list[BaseNode]:
    """The identifiers `node` binds: an assignment's or a loop's target, `as`, walrus.

    A comprehension's variable counts too: it shadows a parameter of that name.
    """
    if node.type in ("assignment", "augmented_assignment", "for_statement", "for_in_clause"):
        target = node.child_by_field_name("left")
    elif node.type == "named_expression":
        target = node.child_by_field_name("name")
    elif node.type in ("as_pattern_target",):
        target = node
    else:
        return []
    if target is None:
        return []
    target = _unwrap(target)
    if target.type == "identifier":
        return [target]
    return [c for c in target.named_children if c.type == "identifier"]


def invoked_slots(
    func: BaseNode, code: bytes, skip_first: bool, body: _Body | None = None
) -> list[int | str]:
    """The slots of each own parameter `func` calls (CV-4): its position, then its name.

    `skip_first` drops a method's receiver (`self`, `cls`); a staticmethod passes False.
    A parameter bound in the body before its first call holds something else there.
    """
    body = body or _Body(func, code)
    calls, binds = body.first_call, body.first_bind
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


def local_names(func: BaseNode, code: bytes, body: _Body | None = None) -> frozenset[str]:
    """The names `func` binds itself: its parameters and every name its body assigns."""
    params = {name for name, _, _ in _parameters(func, code, skip_first=False)}
    return frozenset(params | set((body or _Body(func, code)).first_bind))


def _callee(call: BaseNode) -> BaseNode | None:
    """What `call` calls, parentheses removed: `(cb)()` calls `cb`."""
    callee = call.child_by_field_name("function")
    return _unwrap(callee) if callee is not None else None


def _single_name(target: BaseNode) -> BaseNode:
    """A binding target that is one name in parentheses, as that name: `(cb) = ...`.

    tree-sitter reads `(cb)` on the left as a one-element `tuple_pattern`; only a
    comma (`(cb,) = ...`) makes it an unpacking.
    """
    target = _unwrap(target)
    names = target.named_children
    if (
        target.type in ("tuple_pattern", "pattern_list")
        and len(names) == 1
        and not any(c.type == "," for c in target.children)
    ):
        return _single_name(names[0])
    return target


def _unwrap(expr: BaseNode) -> BaseNode:
    """`expr` without the parentheses around it: `(self.handle)` names `self.handle`."""
    while expr.type == "parenthesized_expression" and len(expr.named_children) == 1:
        expr = expr.named_children[0]
    return expr


def value_name(
    expr: BaseNode, code: bytes, file_names: frozenset[str], shadowed: frozenset[str]
) -> str | None:
    """The raw name of the function value `expr` names, or None if it names none.

    `file_names` are the module-level definitions and imports of the file; a bare
    name counts only when it is one of them and no local of the function shadows it.
    """
    expr = _unwrap(expr)
    if expr.type == "identifier":
        name = get_identifier(expr, code)
        return name if name in file_names and name not in shadowed else None
    if expr.type == "attribute":
        return _attribute_value(expr, code, file_names, shadowed)
    if expr.type == "call":
        first = _partial_of(expr, code)
        return value_name(first, code, file_names, shadowed) if first is not None else None
    return None


def _attribute_value(
    expr: BaseNode, code: bytes, file_names: frozenset[str], shadowed: frozenset[str]
) -> str | None:
    """`self.m`, or a dotted name whose head the file defines or imports."""
    dotted = _dotted(expr, code)
    if dotted is None:
        return None
    head = dotted.partition(".")[0]
    if head == "self" or (head in file_names and head not in shadowed):
        return dotted
    return None


def _partial_of(call: BaseNode, code: bytes) -> BaseNode | None:
    """The function `partial(f, ...)` binds, or None for any other call."""
    callee = call.child_by_field_name("function")
    args = call.child_by_field_name("arguments")
    if callee is None or args is None or _dotted(callee, code) not in _PARTIAL:
        return None
    first = next((a for a in args.named_children if a.type != "comment"), None)
    if first is None or first.type in ("keyword_argument", "list_splat", "dictionary_splat"):
        return None
    return first


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


def _arguments(call: BaseNode, code: bytes) -> Iterator[tuple[int | str, BaseNode]]:
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
                yield get_identifier(name, code), value
            continue
        if arg.type in ("list_splat", "dictionary_splat"):
            position = None
            continue
        if position is not None:
            yield position, arg
            position += 1


def _values_of(
    value: BaseNode,
    code: bytes,
    file_names: frozenset[str],
    shadowed: frozenset[str],
    held: Held | None,
) -> list[str]:
    """The function values one argument hands over: a held local's, or its own name."""
    value = _unwrap(value)
    names = held(value) if held is not None and value.type == "identifier" else []
    if names:
        return names
    name = value_name(value, code, file_names, shadowed)
    return [name] if name is not None else []


def value_candidates(
    func: BaseNode,
    code: bytes,
    source_id: str,
    file_path: str,
    file_names: frozenset[str],
    held: Held | None = None,
    body: _Body | None = None,
) -> list[Edge]:
    """A `value-arg:` candidate per function value `func` hands to a call (CV-1, CV-4).

    A local holding values (CV-2, `held`) passes what it holds, not its own name.
    """
    body = body or _Body(func, code)
    shadowed = local_names(func, code, body)
    edges: list[Edge] = []
    for call in body.calls:
        handed = [
            (slot, value, names)
            for slot, value in _arguments(call, code)
            if (names := _values_of(value, code, file_names, shadowed, held))
        ]
        site = _invoker_site(call, code, shadowed) if handed else None
        if site is None:
            continue
        for slot, value, names in handed:
            context = f"{VALUE_ARG_CONTEXT}{slot}:{site}"
            numbered = len(names) > 1
            edges.extend(
                _value_edge(source_id, file_path, value, name, context, i if numbered else None)
                for i, name in enumerate(names)
            )
    return edges


def _invoker_site(call: BaseNode, code: bytes, shadowed: frozenset[str]) -> str | None:
    """`<raw name>@<row>:<col>` of a call that may invoke, or None.

    A local called here is no invoker (`sorted` as a parameter), and a call whose
    callee has no name is none either.
    """
    callee = _callee(call)
    raw = get_identifier(callee, code) if callee is not None else "unknown"
    if raw == "unknown" or raw in shadowed:
        return None
    return f"{raw}@{call.start_point.row}:{call.start_point.column}"


def _value_edge(
    source_id: str, file_path: str, value: BaseNode, name: str, context: str, i: int | None
) -> Edge:
    """A raw CALLS candidate for value `name` at `value`; `i` numbers one of several."""
    suffix = "" if i is None else f"_{i}"
    return Edge(
        id=f"{file_path}:value_{value.start_byte}_{value.end_byte}{suffix}",
        source=source_id,
        target=f"{RAW_CALL_PREFIX}{name}",
        type=EdgeType.CALLS,
        confidence=0.1,
        context=context,
        file_path=file_path,
        line_number=value.start_point.row + 1,
    )


class HeldValues:
    """What each local of one function holds where it is read (CV-2).

    One pass in source order, as Go's: a binding in the block that first bound the
    local replaces what it held, one in a nested block (an if, a loop) adds to it.
    """

    def __init__(
        self,
        func: BaseNode,
        code: bytes,
        file_names: frozenset[str],
        shadowed: frozenset[str] | None = None,
    ) -> None:
        """Walk `func`'s body once and record the values at every read of a local."""
        self._code = code
        self._file_names = file_names
        self._shadowed = local_names(func, code) if shadowed is None else shadowed
        self._values: dict[str, list[str]] = {}
        self._home: dict[str, int] = {}
        self._reads: dict[int, list[str]] = {}
        body = func.child_by_field_name("body")
        if body is not None:
            self._visit(body, body.start_byte)

    def at(self, node: BaseNode) -> list[str]:
        """The function values the local read at `node` holds there; [] if none."""
        return list(self._reads.get(node.start_byte, ()))

    def _visit(self, node: BaseNode, block: int) -> None:
        """Record reads in `node`, then bind what it assigns, as Python evaluates it."""
        if node.type in _OWN_SCOPES:
            return
        if node.type == "block":
            block = node.start_byte
        if node.type == "identifier":
            name = get_identifier(node, self._code)
            if self._values.get(name):
                self._reads[node.start_byte] = list(self._values[name])
            return
        if node.type == "assignment":
            self._assign(node, block)
            return
        if node.type == "for_statement":
            self._loop(node, block)
            return
        if node.type in _REBINDS:
            self._rebind(node, block)
            return
        for child in _read_children(node):
            self._visit(child, block)

    def _rebind(self, node: BaseNode, block: int) -> None:
        """A walrus binds its value; `x += ...` and an `as` target bind no function value."""
        if node.type == "as_pattern_target":
            self._clear(node, block)
            return
        value = node.child_by_field_name("value" if node.type == "named_expression" else "right")
        if value is not None:
            self._visit(value, block)
        target = node.child_by_field_name("name" if node.type == "named_expression" else "left")
        if target is None:
            return
        if node.type == "named_expression" and target.type == "identifier":
            self._bind(get_identifier(target, self._code), self._held_by(value), block)
        else:
            self._clear(target, block)

    def _clear(self, target: BaseNode, block: int) -> None:
        """Every name `target` binds now holds no function value we can name."""
        for name in _target_names(target):
            self._bind(get_identifier(name, self._code), [], block)

    def _assign(self, node: BaseNode, block: int) -> None:
        """`x = <value>`: the right side is read first, then `x` bound to it."""
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if right is not None:
            self._visit(right, block)
            right = _unwrap(right)
        if left is None:
            return
        left = _single_name(left)
        if left.type == "identifier":
            self._bind(get_identifier(left, self._code), self._held_by(right), block)
            return
        pairs = _pairs(left, right)
        if pairs is None:
            self._clear(left, block)  # an unpacking we cannot pair holds nothing we know
            return
        # `a, b = x, y`: every right side was read above, before any name is bound.
        for target, value in pairs:
            self._bind(get_identifier(target, self._code), self._held_by(value), block)

    def _loop(self, node: BaseNode, block: int) -> None:
        """`for cb in (a, b)`: `cb` holds each value the literal names."""
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if right is not None:
            self._visit(right, block)
            right = _unwrap(right)
        left = _single_name(left) if left is not None else None
        if left is not None and left.type == "identifier":
            elements = right.named_children if right is not None and right.type in _LITERALS else []
            values = [v for e in elements for v in self._held_by(e)]
            self._bind(get_identifier(left, self._code), values, block)
        elif left is not None:
            self._clear(left, block)
        for child in node.children:
            if child.type in ("block", "else_clause"):
                self._visit(child, block)

    def _held_by(self, expr: BaseNode | None) -> list[str]:
        """The values `expr` names: a held local's, or the value form's own name."""
        if expr is None:
            return []
        expr = _unwrap(expr)
        if expr.type == "identifier" and expr.start_byte in self._reads:
            return list(self._reads[expr.start_byte])
        name = value_name(expr, self._code, self._file_names, self._shadowed)
        return [name] if name is not None else []

    def _bind(self, name: str, values: list[str], block: int) -> None:
        """Bind `name` in `block`: replace in its home block, add anywhere else."""
        home = self._home.setdefault(name, block)
        if home == block:
            self._values[name] = values
        else:
            self._values[name] = list(dict.fromkeys([*self._values.get(name, []), *values]))


#: Bindings other than `=` and `for`, which HeldValues reads on their own.
_REBINDS = frozenset({"augmented_assignment", "named_expression", "as_pattern_target"})
#: Right sides whose elements pair with an unpacking's targets one by one.
_SEQUENCES = frozenset({"expression_list", "tuple", "list"})


def _pairs(left: BaseNode, right: BaseNode | None) -> list[tuple[BaseNode, BaseNode]] | None:
    """`a, b = x, y` as [(a, x), (b, y)]; None unless both sides are flat and equal in length.

    Only a literal's elements pair: `a, b = mk()` or `a, b = obj.attr` unpack a value
    whose parts the walk cannot see, however many children their nodes happen to have.
    """
    if right is None or right.type not in _SEQUENCES:
        return None
    targets, values = left.named_children, right.named_children
    if len(targets) != len(values) or any(t.type != "identifier" for t in targets):
        return None
    if any(v.type == "list_splat" for v in values):
        return None
    return list(zip(targets, values, strict=True))


def _target_names(target: BaseNode) -> list[BaseNode]:
    """The names a binding target binds, at any depth: not an attribute's or subscript's."""
    if target.type == "identifier":
        return [target]
    if target.type in ("attribute", "subscript"):
        return []
    return [n for child in target.named_children for n in _target_names(child)]


def _read_children(node: BaseNode) -> list[BaseNode]:
    """The children of `node` that read names: not an attribute's or a keyword's own name."""
    if node.type == "attribute":
        obj = node.child_by_field_name("object")
        return [obj] if obj is not None else []
    if node.type == "keyword_argument":
        value = node.child_by_field_name("value")
        return [value] if value is not None else []
    return list(node.children)


def held_values(func: BaseNode, code: bytes, file_names: frozenset[str]) -> HeldValues:
    """The values each local of `func` holds where it is read (CV-2)."""
    return HeldValues(func, code, file_names)


def local_value_calls(
    func: BaseNode,
    source_id: str,
    file_path: str,
    held: HeldValues,
    calls: list[BaseNode] | None = None,
) -> list[Edge]:
    """A `value-local:` candidate per value a local holds where `func` calls it (CV-2)."""
    edges: list[Edge] = []
    for call in calls if calls is not None else [n for n in _own_nodes(func) if n.type == "call"]:
        callee = _callee(call)
        if callee is None or callee.type != "identifier":
            continue
        for i, name in enumerate(held.at(callee)):
            edges.append(
                Edge(
                    id=f"{file_path}:value_{call.start_byte}_{call.end_byte}_{i}",
                    source=source_id,
                    target=f"{RAW_CALL_PREFIX}{name}",
                    type=EdgeType.CALLS,
                    confidence=0.1,
                    context=VALUE_LOCAL_CONTEXT,
                    file_path=file_path,
                    line_number=call.start_point.row + 1,
                )
            )
    return edges


#: Every node `function_values` reads, found in one pass in C rather than a walk in Python.
_EVENTS = Query(
    Language(tspython.language()),
    """
    (function_definition) @def
    [(lambda) (class_definition) (decorated_definition)] @scope
    [(call) (assignment) (augmented_assignment) (for_statement) (named_expression)
     (as_pattern_target) (for_in_clause)] @event
    """,
)


def _bodies(root: BaseNode) -> Iterator[tuple[BaseNode, list[BaseNode]]]:
    """Each function definition with the calls and bindings of its own body, in order.

    One sweep in source order: an event belongs to the innermost scope open at its
    start, and counts only when that scope is a function and the event is in its body
    (a default value or a decorator is not).
    """
    captured = QueryCursor(_EVENTS).captures(root)
    defs = captured.get("def", [])
    scopes = _Scopes(sorted([*defs, *captured.get("scope", [])], key=_outer_first))
    owned: dict[int, list[BaseNode]] = {d.start_byte: [] for d in defs}
    for event in sorted(captured.get("event", []), key=_outer_first):
        owner = scopes.innermost(event.start_byte)
        if owner is not None and _in_body(owner, event):
            owned[owner.start_byte].append(event)
    for definition in defs:
        yield definition, owned[definition.start_byte]


class _Scopes:
    """The scopes open at a point of the source, for points visited in order."""

    def __init__(self, ordered: list[BaseNode]) -> None:
        """Scopes in source order, an enclosing one before what it encloses."""
        self._pending = iter(ordered)
        self._upcoming = next(self._pending, None)
        self._open: list[BaseNode] = []

    def innermost(self, at: int) -> BaseNode | None:
        """The innermost scope open at byte `at`; `at` never moves backwards."""
        while self._upcoming is not None and self._upcoming.start_byte <= at:
            self._close_before(self._upcoming.start_byte)
            self._open.append(self._upcoming)
            self._upcoming = next(self._pending, None)
        self._close_before(at)
        return self._open[-1] if self._open else None

    def _close_before(self, at: int) -> None:
        """Drop the scopes that end at or before byte `at`."""
        while self._open and self._open[-1].end_byte <= at:
            self._open.pop()


def _in_body(scope: BaseNode, event: BaseNode) -> bool:
    """Whether `event` is in function `scope`'s body (not a default or a decorator)."""
    if scope.type != "function_definition":
        return False
    body = scope.child_by_field_name("body")
    return body is not None and event.start_byte >= body.start_byte


def _outer_first(node: BaseNode) -> tuple[int, int]:
    """Source order, an enclosing node before what it encloses."""
    return node.start_byte, -node.end_byte


def _with_invokes(owner: Node, slots: list[int | str]) -> Node:
    """`owner` carrying `slots`; without any when there are none.

    Two definitions of one name share a node, and the later one is what runs: its
    slots replace the earlier one's, and none clears them.
    """
    metadata = {k: v for k, v in owner.metadata.items() if k != "invokes"}
    if slots:
        metadata["invokes"] = slots
    return owner if metadata == owner.metadata else owner.model_copy(update={"metadata": metadata})


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
    for node, events in _bodies(root):
        position = index.get(get_id(node, code, file_path, source_root))
        if position is None:
            continue
        owner = updated[position]
        static = "staticmethod" in owner.metadata.get("decorators", ())
        body = _Body(node, code, events)
        skip_first = owner.type is NodeType.METHOD and not static
        slots = invoked_slots(node, code, skip_first, body)
        updated[position] = _with_invokes(owner, slots)
        if not body.calls:
            continue
        if not body.holds:
            edges.extend(value_candidates(node, code, owner.id, file_path, file_names, body=body))
            continue
        held = HeldValues(node, code, file_names, local_names(node, code, body))
        edges.extend(value_candidates(node, code, owner.id, file_path, file_names, held.at, body))
        edges.extend(local_value_calls(node, owner.id, file_path, held, body.calls))
    return updated, edges
