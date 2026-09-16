"""Which JavaScript runtime globals a TypeScript file rebinds (#111).

The resolver rewrites a call on `history` or `fetch` to the runtime's
`js_builtins.*` — unless the file has its own `history`: an import, a `const`,
a parameter, a caught error. It cannot tell from a call name alone, and the
TypeScript extractor records no import map, so the extractor answers here.

Scope is the whole file, not the lexical block. That over-approximates on
purpose: a name bound anywhere in the file keeps every call on it UNKNOWN, which
is where it stood before #111. The opposite error — a confident STDLIB edge for
the file's own value — is the one that skews the unresolved ratio.
"""

from collections.abc import Callable

from tree_sitter import Node as TSNode

from cgis.core.js_globals import JS_GLOBALS

#: Nodes whose field holds a binding pattern, and that field's name.
_PATTERN_FIELDS: dict[str, str] = {
    "variable_declarator": "name",
    "required_parameter": "pattern",
    "optional_parameter": "pattern",
    "arrow_function": "parameter",
    "catch_clause": "parameter",
    "for_in_statement": "left",
}

#: Declarations whose `name` field binds a value in the enclosing scope.
_NAMED_DECLARATIONS: frozenset[str] = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "enum_declaration",
        "internal_module",  # namespace crypto { }
        "module",  # module history { }  — a quoted `declare module 'x'` binds nothing
    }
)

#: `import crypto = require('crypto')` and `import history = X.Y`.
_IMPORT_EQUALS: frozenset[str] = frozenset({"import_require_clause", "import_alias"})


def shadowed_globals(root: TSNode) -> list[str]:
    """The JS globals `root`'s file binds itself, sorted for a deterministic graph."""
    bound: set[str] = set()
    _collect(root, bound)
    return sorted(bound & JS_GLOBALS)


def _collect(node: TSNode, bound: set[str]) -> None:
    """Walk the tree, adding every name introduced by a binding position.

    Iterative, not recursive: a long `'a' + 'b' + ...` chain nests one level per
    operand, and a generated file with a couple of thousand of them exceeds
    Python's recursion limit — a file the extractor otherwise parses fine.
    Visit order does not matter; the result is a set.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        binder = _BINDERS.get(current.type)
        if binder is not None:
            binder(current, bound)
        stack.extend(current.children)


def _collect_import_clause(clause: TSNode, bound: set[str]) -> None:
    """Default, namespace and named imports; an alias binds the alias, not the original."""
    for child in clause.children:
        if child.type == "identifier":
            _add(child, bound)
        elif child.type == "namespace_import":
            _add_first_identifier(child, bound)
        elif child.type == "named_imports":
            for spec in child.children:
                _bind_import_specifier(spec, bound)


def _bind_import_specifier(spec: TSNode, bound: set[str]) -> None:
    """`{ fetch }` binds `fetch`; `{ fetch as f }` binds only `f`."""
    if spec.type != "import_specifier":
        return
    local = spec.child_by_field_name("alias") or spec.child_by_field_name("name")
    if local is not None:
        _add(local, bound)


def _add_first_identifier(node: TSNode, bound: set[str]) -> None:
    """`* as crypto`, `import crypto = require(...)`: the local name is the first identifier."""
    local = next((c for c in node.children if c.type == "identifier"), None)
    if local is not None:
        _add(local, bound)


def _bind_declaration_name(node: TSNode, bound: set[str]) -> None:
    """A declaration's own name; `namespace history.v2 { }` binds its first segment."""
    name = node.child_by_field_name("name")
    if name is None:
        return
    if name.type in ("identifier", "type_identifier"):
        _add(name, bound)
    elif name.type == "nested_identifier" and name.text is not None:
        bound.add(name.text.decode("utf-8").split(".", maxsplit=1)[0])


def _pattern_binder(field: str) -> Callable[[TSNode, set[str]], None]:
    """A binder that collects the pattern held in `field`."""

    def bind(node: TSNode, bound: set[str]) -> None:
        """Collect the names the node's `field` pattern binds."""
        pattern = node.child_by_field_name(field)
        if pattern is not None:
            _collect_pattern(pattern, bound)

    return bind


def _collect_pattern(pattern: TSNode, bound: set[str]) -> None:
    """Names a destructuring or plain pattern binds.

    `{ a: b }` binds `b`, never the key `a`; a default value (`{ a = fetch }`) is
    an expression, not a binding, so only the left of an assignment pattern counts.
    """
    if pattern.type in ("identifier", "shorthand_property_identifier_pattern"):
        _add(pattern, bound)
    elif pattern.type == "pair_pattern":
        value = pattern.child_by_field_name("value")
        if value is not None:
            _collect_pattern(value, bound)
    elif pattern.type in ("assignment_pattern", "object_assignment_pattern"):
        left = pattern.child_by_field_name("left")
        if left is not None:
            _collect_pattern(left, bound)
    elif pattern.type in ("object_pattern", "array_pattern", "rest_pattern"):
        for child in pattern.named_children:
            _collect_pattern(child, bound)


def _add(identifier: TSNode, bound: set[str]) -> None:
    """Record one identifier's text."""
    if identifier.text is not None:
        bound.add(identifier.text.decode("utf-8"))


#: Node type -> how that node binds names. Built last, from the helpers above.
_BINDERS: dict[str, Callable[[TSNode, set[str]], None]] = {
    "import_clause": _collect_import_clause,
    **dict.fromkeys(_IMPORT_EQUALS, _add_first_identifier),
    **dict.fromkeys(_NAMED_DECLARATIONS, _bind_declaration_name),
    **{node_type: _pattern_binder(field) for node_type, field in _PATTERN_FIELDS.items()},
}
