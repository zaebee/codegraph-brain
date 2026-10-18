"""Data keys in Python: the strings a file uses to address data, and how it uses each.

The clauses are docs/specs/core/ontology.yml DK-1..DK-3. A key is a plain string
literal in a key position (`d["k"]`, `d.get("k")`, `"k" in d`, a field of a query or
of a document handed to storage, an annotated field of a model); each becomes a
`raw_key:<namespace>|<path>` edge the resolver turns into a key node. Nested access
(`doc["details"]["brand"]`) is one dotted path. Which calls read, write or name a
namespace is `KEY_PROFILE`. The file is read by one tree-sitter query, its captures
assigned to their owners in one sweep, as `_values._bodies` does (#701).
"""

from collections.abc import Callable, Iterator

import tree_sitter_python as tspython
from tree_sitter import Language, Query, QueryCursor
from tree_sitter import Node as BaseNode

from cgis.core.models import RAW_KEY_PREFIX, Edge, EdgeType
from cgis.languages.python._values import _outer_first, _Scopes
from cgis.languages.python.profile import KEY_PROFILE

_READ, _WRITE, _DECLARE = EdgeType.READS_KEY, EdgeType.WRITES_KEY, EdgeType.DECLARES_KEY
#: What reading a key through each dict method does to it.
_DICT_METHODS: dict[str, tuple[EdgeType, ...]] = {
    "get": (_READ,),
    "pop": (_READ, _WRITE),
    "setdefault": (_WRITE,),
}
#: Right sides of `in` that are constants, not data: `"b" in ["x", "y"]`, `"a" in "abc"`.
_LITERAL_COLLECTIONS = frozenset({"list", "tuple", "set", "string", "concatenated_string"})
_REDIS = KEY_PROFILE.redis_reads | KEY_PROFILE.redis_writes
#: Nodes a store target sits inside on the left of `=` or after `del`.
_TARGET_WRAPPERS = frozenset(
    {"pattern_list", "tuple_pattern", "list_pattern", "expression_list", "parenthesized_expression"}
)


def _any_of(names: frozenset[str] | set[str]) -> str:
    return " ".join(f'"{n}"' for n in sorted(names))


_METHODS = {*_DICT_METHODS, *_REDIS, *KEY_PROFILE.task_methods}
#: Every node a key can sit in, and every scope that owns one, in one query.
_KEYS = Query(
    Language(tspython.language()),
    f"""
    (subscript subscript: (string)) @sub
    ((call function: (attribute attribute: (identifier) @_m)
           arguments: (argument_list . (string))) @mcall
     (#any-of? @_m {_any_of(_METHODS)}))
    ((call function: (identifier) @_f arguments: (argument_list . (string))) @fcall
     (#any-of? @_f {_any_of(KEY_PROFILE.task_methods)}))
    (comparison_operator . (string)) @member
    ((call function: (attribute attribute: (identifier) @_s)
           arguments: (argument_list (keyword_argument value: [(dictionary) (list)]))) @store
     (#any-of? @_s {_any_of(set(KEY_PROFILE.storage_calls))}))
    (pair key: (string (string_content) @_op
                       (#any-of? @_op {_any_of(KEY_PROFILE.query_operators)}))) @op
    (class_definition superclasses: (argument_list)) @model
    [(function_definition) (class_definition) (decorated_definition)] @scope
    (type) @type
    """,
)

#: One key use found: (anchor, relation, namespace, path).
_Use = tuple[BaseNode, EdgeType, str, str]


def _text(node: BaseNode, code: bytes) -> str:
    return code[node.start_byte : node.end_byte].decode("utf8", errors="replace")


def _literal(node: BaseNode | None, code: bytes) -> str | None:
    """The text of a plain, non-empty string literal; None for anything else (an f-string)."""
    if node is None or node.type != "string" or node.child_count != 3:
        return None
    start, content, _ = node.children
    if content.type != "string_content" or b"f" in code[start.start_byte : start.end_byte].lower():
        return None
    return _text(content, code)


def _unwrap(expr: BaseNode) -> BaseNode:
    while expr.type == "parenthesized_expression" and expr.named_child_count == 1:
        expr = expr.named_children[0]
    return expr


def _span(node: BaseNode) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


class _Access:
    """One dict access: the receiver, the key, what it does, and whether a path continues it."""

    def __init__(
        self, node: BaseNode, receiver: BaseNode, key: str, does: tuple[EdgeType, ...]
    ) -> None:
        """Note an access of `key` on `receiver` at `node`."""
        self.node = node
        self.receiver = _unwrap(receiver)
        self.key = key
        self.does = does
        #: Whether this access is only the head of a longer one (`doc["a"]` in `doc["a"]["b"]`).
        self.continued = False
        self._path: str | None = None

    def path(self, accesses: dict[tuple[int, int], "_Access"]) -> str:
        """The dotted path from the first access in the chain to this one."""
        if self._path is None:
            head = accesses.get(_span(self.receiver))
            self._path = _join(head.path(accesses) if head else "", self.key)
        return self._path


#: Dict accesses by the span of their node, so a chain finds its head by its receiver.
_Accesses = dict[tuple[int, int], _Access]


def _subscripts(
    subscripts: list[BaseNode], types: list[BaseNode], code: bytes
) -> Iterator[_Access]:
    """`d["k"]` outside an annotation: a read, or a write as a store or `del` target."""
    annotations = iter(sorted(types, key=_outer_first))
    annotation = next(annotations, None)
    for node in sorted(subscripts, key=_outer_first):
        while annotation is not None and annotation.end_byte <= node.start_byte:
            annotation = next(annotations, None)
        if annotation is not None and annotation.start_byte <= node.start_byte:
            continue
        value, *rest = [c for c in node.named_children if c.type != "comment"]
        key = _literal(rest[0], code) if len(rest) == 1 else None
        if key is not None and not _text(value, code).endswith("Literal"):
            yield _Access(node, value, key, _store_relations(node))


def _calls(calls: list[BaseNode], code: bytes) -> Iterator[tuple[BaseNode, str, BaseNode | None]]:
    """(call, its first argument's literal, its receiver or None) for a call keyed by a literal."""
    for call in calls:
        function, args = call.child_by_field_name("function"), call.child_by_field_name("arguments")
        key = _literal(args.named_children[0], code) if args is not None else None
        if function is not None and key is not None:
            yield call, key, function.child_by_field_name("object")


def _method(call: BaseNode, code: bytes) -> str:
    """The name a call calls: `get` of `d.get(...)`, `enqueue` of `enqueue(...)`."""
    function = call.child_by_field_name("function")
    return _last_segment(function, code) if function is not None else ""


def _named_calls(calls: list[BaseNode], code: bytes) -> Iterator[_Use]:
    """A Redis method on a redis receiver uses a `redis` key; `enqueue("task")` a `task` key."""
    for call, key, receiver in _calls(calls, code):
        method = _method(call, code)
        if receiver is None or method in KEY_PROFILE.task_methods:
            if method in KEY_PROFILE.task_methods:
                yield call, _WRITE, "task", key
        elif method in _REDIS and "redis" in _last_segment(receiver, code).lower():
            yield call, _READ if method in KEY_PROFILE.redis_reads else _WRITE, "redis", key


def _dict_calls(calls: list[BaseNode], code: bytes) -> Iterator[_Access]:
    """`d.get("k")`, `d.pop("k")`, `d.setdefault("k")`, unless `d` is a redis client."""
    for call, key, receiver in _calls(calls, code):
        method = _method(call, code)
        if receiver is None or method not in _DICT_METHODS:
            continue
        if method in _REDIS and "redis" in _last_segment(receiver, code).lower():
            continue
        yield _Access(call, receiver, key, _DICT_METHODS[method])


def _memberships(comparisons: list[BaseNode], code: bytes) -> Iterator[_Access]:
    """`"k" in d` and `"k" not in d` read `k`, continuing a path `d` names."""
    for node in comparisons:
        if node.child_count != 3 or node.children[1].type not in ("in", "not in"):
            continue
        key = _literal(node.children[0], code)
        # `"b" in ["x", "y"]` tests a constant, not data.
        if key is not None and _unwrap(node.children[2]).type not in _LITERAL_COLLECTIONS:
            yield _Access(node, node.children[2], key, (_READ,))


def _chained(accesses: _Accesses) -> Iterator[_Use]:
    """One use per access chain: its last access, with the whole dotted path."""
    for access in accesses.values():
        head = accesses.get(_span(access.receiver))
        # Only a read continues into a path: `d.pop("a")["b"]` keeps both.
        if head is not None and head.does == (_READ,) and head.node.type != "comparison_operator":
            head.continued = True
    for access in accesses.values():
        if not access.continued:
            path = access.path(accesses)
            yield from ((access.node, relation, "", path) for relation in access.does)


class _Literals:
    """The dict literals of one file that address data: storage documents, query bodies."""

    def __init__(self, code: bytes) -> None:
        """Read literals of `code`."""
        self.code = code

    def stored(self, call: BaseNode) -> Iterator[_Use]:
        """The keys of the literals a storage call is handed, in the call's namespace.

        A write method's document arguments are written; every other literal argument
        (`query=`, `aggs=`, `sort=`, a search's `body=`) is a query and reads its fields.
        """
        function, args = call.child_by_field_name("function"), call.child_by_field_name("arguments")
        attribute = function.child_by_field_name("attribute") if function else None
        if attribute is None or args is None:
            return
        method = _text(attribute, self.code)
        keywords = dict(_keywords(args, self.code))
        namespace_keyword = KEY_PROFILE.storage_calls[method]
        namespace = _namespace(keywords.get(namespace_keyword), self.code)
        for name, value in keywords.items():
            if name == namespace_keyword or value.type not in ("dictionary", "list"):
                continue
            if name in KEY_PROFILE.query_operators:
                yield from self._operands(name, value, namespace)
            elif method in KEY_PROFILE.storage_writes and name in KEY_PROFILE.write_arguments:
                if value.type == "dictionary":
                    yield from self._written(value, namespace, "", top=True)
            else:
                yield from self._queried(value, namespace)

    def _written(
        self, literal: BaseNode, namespace: str, prefix: str, *, top: bool
    ) -> Iterator[_Use]:
        """A written dict literal holds data: each leaf key is written, nested as a dotted path."""
        for pair, key, value in _pairs(literal, self.code):
            if top and key in KEY_PROFILE.write_arguments and value.type == "dictionary":
                yield from self._written(value, namespace, prefix, top=False)
            elif value.type == "dictionary":
                yield from self._written(value, namespace, _join(prefix, key), top=False)
            else:
                key_node = pair.child_by_field_name("key")
                yield key_node or pair, _WRITE, namespace, _join(prefix, key)

    def _queried(self, node: BaseNode, namespace: str) -> Iterator[_Use]:
        """A query body: every operator in it reads its fields in the call's namespace."""
        stack = [node]
        while stack:
            current = stack.pop()
            if current.type == "list":
                stack.extend(current.named_children)
            elif current.type == "dictionary":
                for _pair, key, value in _pairs(current, self.code):
                    if key in KEY_PROFILE.query_operators:
                        yield from self._operands(key, value, namespace)
                    else:
                        stack.append(value)

    def operator(self, pair: BaseNode, namespace: str) -> Iterator[_Use]:
        """The fields an operator pair (`{"term": {"f": x}}`) reads."""
        name = _literal(pair.child_by_field_name("key"), self.code)
        value = pair.child_by_field_name("value")
        if name is not None and value is not None:
            yield from self._operands(name, value, namespace)

    def _operands(self, name: str, value: BaseNode, namespace: str) -> Iterator[_Use]:
        """`{"f": x}` and `{"field": "f"}` read `f`; only a sort reads a bare string as a field."""
        elements = value.named_children if value.type == "list" else [value]
        for element in elements:
            if element.type == "dictionary":
                yield from self._operator_fields(element, namespace)
            elif name == "sort" and (field := _literal(element, self.code)) is not None:
                yield element, _READ, namespace, field

    def _operator_fields(self, literal: BaseNode, namespace: str) -> Iterator[_Use]:
        """The fields an operator's dict names: its `"field"` value, else its keys."""
        pairs = list(_pairs(literal, self.code))
        for _, key, value in pairs:
            field = _literal(value, self.code) if key == "field" else None
            if field is not None:
                yield value, _READ, namespace, field
                return
        for pair, key, _ in pairs:
            yield pair.child_by_field_name("key") or pair, _READ, namespace, key


def _model_uses(
    classes: list[BaseNode], code: bytes, owners: Callable[[BaseNode], str | None]
) -> Iterator[_Use]:
    """Each annotated public field of a model class declares a key of the model."""
    for cls in classes:
        bases = cls.child_by_field_name("superclasses")
        body = cls.child_by_field_name("body")
        if (
            bases is None
            or body is None
            or not any(
                _last_segment(b, code) in KEY_PROFILE.model_bases for b in bases.named_children
            )
        ):
            continue
        namespace = owners(cls)
        if namespace is None:
            continue
        for name in _fields(body, code):
            yield name, _DECLARE, namespace, _text(name, code)


def _store_relations(node: BaseNode) -> tuple[EdgeType, ...]:
    """What a subscript does to its key: a store or `del` target writes, `+=` both."""
    child, parent = node, node.parent
    while parent is not None and parent.type in _TARGET_WRAPPERS:
        child, parent = parent, parent.parent
    if parent is None:
        return (_READ,)
    if parent.type == "delete_statement":
        return (_WRITE,)
    left = parent.child_by_field_name("left")
    if left is None or _span(left) != _span(child):
        return (_READ,)
    if parent.type == "assignment":
        return (_WRITE,)
    if parent.type == "augmented_assignment":
        return (_READ, _WRITE)
    return (_READ,)


def _last_segment(expr: BaseNode, code: bytes) -> str:
    """`redis` of `self.redis`, `BaseModel` of `pydantic.BaseModel`; "" for anything else."""
    if expr.type == "attribute":
        attribute = expr.child_by_field_name("attribute")
        return _text(attribute, code) if attribute is not None else ""
    return _text(expr, code) if expr.type == "identifier" else ""


def _keywords(args: BaseNode, code: bytes) -> Iterator[tuple[str, BaseNode]]:
    for arg in args.named_children:
        if arg.type == "keyword_argument":
            name, value = arg.child_by_field_name("name"), arg.child_by_field_name("value")
            if name is not None and value is not None:
                yield _text(name, code), value


def _namespace(value: BaseNode | None, code: bytes) -> str:
    """A namespace named at the call: a literal, `@NAME` for a constant, else unscoped."""
    if value is None:
        return ""
    if value.type == "identifier":
        return f"@{_text(value, code)}"
    literal = _literal(value, code)
    # The resolver splits a raw key at its first `|` and reads `@` as a constant.
    return (
        literal
        if literal is not None and "|" not in literal and not literal.startswith("@")
        else ""
    )


def _pairs(literal: BaseNode, code: bytes) -> Iterator[tuple[BaseNode, str, BaseNode]]:
    """(pair, key, value) for each entry of a dict literal keyed by a plain string."""
    for pair in literal.named_children:
        if pair.type != "pair":
            continue
        key, value = (
            _literal(pair.child_by_field_name("key"), code),
            pair.child_by_field_name("value"),
        )
        if key is not None and value is not None:
            yield pair, key, value


def _fields(body: BaseNode, code: bytes) -> Iterator[BaseNode]:
    """The names of a class body's annotated public fields: not `_private`, not a ClassVar."""
    for statement in body.named_children:
        if statement.type != "expression_statement" or statement.named_child_count != 1:
            continue
        assignment = statement.named_children[0]
        name = assignment.child_by_field_name("left")
        annotation = assignment.child_by_field_name("type")
        if assignment.type != "assignment" or name is None or annotation is None:
            continue
        if name.type != "identifier" or _text(name, code).startswith("_"):
            continue
        if _text(annotation, code).removeprefix("typing.").startswith("ClassVar"):
            continue
        yield name


def _outside(spans: list[tuple[int, int]], node: BaseNode) -> bool:
    return not any(start <= node.start_byte < end for start, end in spans)


def key_edges(
    root: BaseNode, code: bytes, file_path: str, owners: Callable[[BaseNode], str | None]
) -> list[Edge]:
    """A `raw_key:` edge per key use in the file, sourced at the code that owns it.

    `owners` maps a node to the id of what owns the code there: the enclosing
    function, else class, else module (the walk's rule). It is asked once per scope:
    a definition, a decorated definition (its decorators belong to the definition)
    or the module root.
    """
    captured = QueryCursor(_KEYS).captures(root)
    accesses: _Accesses = {
        _span(access.node): access
        for found in (
            _subscripts(captured.get("sub", []), captured.get("type", []), code),
            _dict_calls(captured.get("mcall", []), code),
            _memberships(captured.get("member", []), code),
        )
        for access in found
    }
    stores = list({_span(c): c for c in captured.get("store", [])}.values())
    # A storage call reads its keyword literals itself; a positional one reads unscoped.
    covered = [
        _span(arg)
        for c in stores
        if (args := c.child_by_field_name("arguments"))
        for arg in args.named_children
        if arg.type == "keyword_argument"
    ]
    literals = _Literals(code)
    uses = [
        *_chained(accesses),
        *_named_calls([*captured.get("mcall", []), *captured.get("fcall", [])], code),
        *(use for call in stores for use in literals.stored(call)),
        *(
            use
            for p in captured.get("op", [])
            if _outside(covered, p)
            for use in literals.operator(p, "")
        ),
        *_model_uses(captured.get("model", []), code, owners),
    ]
    return _owned_edges(uses, captured.get("scope", []), root, file_path, owners)


def _owned_edges(
    uses: list[_Use],
    scopes: list[BaseNode],
    root: BaseNode,
    file_path: str,
    owners: Callable[[BaseNode], str | None],
) -> list[Edge]:
    """Each use as an edge from its owner: one sweep in source order over the scopes."""
    open_scopes = _Scopes(sorted(scopes, key=_outer_first))
    named: dict[tuple[int, int], str | None] = {}
    edges: list[Edge] = []
    for anchor, relation, namespace, path in sorted(uses, key=lambda u: _outer_first(u[0])):
        scope = open_scopes.innermost(anchor.start_byte) or root
        if _span(scope) not in named:
            named[_span(scope)] = owners(scope)
        source = named[_span(scope)]
        if not source:
            continue
        edges.append(
            Edge(
                id=f"{file_path}:key_{anchor.start_byte}_{anchor.end_byte}_{relation.value}",
                source=source,
                target=f"{RAW_KEY_PREFIX}{namespace}|{path}",
                type=relation,
                confidence=1.0,
                file_path=file_path,
                line_number=anchor.start_point.row + 1,
            )
        )
    return edges


def string_constants(root: BaseNode, code: bytes) -> dict[str, str]:
    """Each module-level `NAME = "literal"`, by name; a name bound twice holds its later value.

    Recorded on the FILE node, where the resolver reads the namespace a constant names
    (`index=EVENTS`), in this file or through another file's import of it.
    """
    found: dict[str, str] = {}
    for statement in root.named_children:
        if statement.type != "expression_statement" or statement.named_child_count != 1:
            continue
        assignment = statement.named_children[0]
        if assignment.type != "assignment" or assignment.child_by_field_name("type") is not None:
            continue
        name = assignment.child_by_field_name("left")
        value = _literal(assignment.child_by_field_name("right"), code)
        if name is not None and name.type == "identifier" and value is not None:
            found[_text(name, code)] = value
    return found
