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

#: `import crypto = require('crypto')` and `import history = X.Y`: the local name
#: is the node's first identifier child.
_IMPORT_EQUALS: frozenset[str] = frozenset({"import_require_clause", "import_alias"})


def shadowed_globals(root: TSNode) -> list[str]:
    """The JS globals `root`'s file binds itself, sorted for a deterministic graph."""
    bound: set[str] = set()
    _collect(root, bound)
    return sorted(bound & JS_GLOBALS)


def _collect(node: TSNode, bound: set[str]) -> None:
    """Walk the tree, adding every name introduced by a binding position."""
    if node.type == "import_clause":
        _collect_import_clause(node, bound)
    elif node.type in _IMPORT_EQUALS:
        local = next((c for c in node.children if c.type == "identifier"), None)
        if local is not None:
            _add(local, bound)
    elif node.type in _NAMED_DECLARATIONS:
        name = node.child_by_field_name("name")
        if name is not None and name.type in ("identifier", "type_identifier"):
            _add(name, bound)
        elif name is not None and name.type == "nested_identifier" and name.text is not None:
            # namespace history.v2 { } binds `history`
            bound.add(name.text.decode("utf-8").split(".", maxsplit=1)[0])
    field = _PATTERN_FIELDS.get(node.type)
    if field is not None:
        pattern = node.child_by_field_name(field)
        if pattern is not None:
            _collect_pattern(pattern, bound)
    for child in node.children:
        _collect(child, bound)


def _collect_import_clause(clause: TSNode, bound: set[str]) -> None:
    """Default, namespace and named imports; an alias binds the alias, not the original."""
    for child in clause.children:
        if child.type == "identifier":
            _add(child, bound)
        elif child.type == "namespace_import":
            for inner in child.children:
                if inner.type == "identifier":
                    _add(inner, bound)
        elif child.type == "named_imports":
            for spec in child.children:
                if spec.type != "import_specifier":
                    continue
                local = spec.child_by_field_name("alias") or spec.child_by_field_name("name")
                if local is not None:
                    _add(local, bound)


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
