"""Readings of tree-sitter-go syntax the Go extractor needs — no node or edge building."""

import re
from collections.abc import Iterator, Mapping

from tree_sitter import Node as TSNode

#: Go's predeclared types: never a type of ours to look methods up on.
_PREDECLARED = frozenset(
    {
        "any",
        "bool",
        "byte",
        "comparable",
        "complex64",
        "complex128",
        "error",
        "float32",
        "float64",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "rune",
        "string",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "uintptr",
    }
)
#: Go's predeclared functions. With the predeclared types (conversions such as
#: `string(b)`), the names a call can reach without any declaration (I4).
_BUILTIN_FUNCTIONS = frozenset(
    {
        "append",
        "cap",
        "clear",
        "close",
        "complex",
        "copy",
        "delete",
        "imag",
        "len",
        "make",
        "max",
        "min",
        "new",
        "panic",
        "print",
        "println",
        "real",
        "recover",
    }
)
BUILTINS = _BUILTIN_FUNCTIONS | _PREDECLARED
_MAJOR_VERSION = re.compile(r"v\d+")
#: Import names that bring a package in without a name to call it by.
UNNAMED_IMPORTS = frozenset({"_", "."})


def text(node: TSNode | None) -> str:
    """The node's source text, or empty."""
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def dotted(import_path: str) -> str:
    """An import path as a dotted FQN: `example.com/app/x` -> `example.com.app.x` (H4)."""
    return import_path.replace("/", ".")


def default_import_name(import_path: str) -> str:
    """The name a package is called by when the import gives none — its last segment.

    Approximate, as Go itself takes the name from the package clause: a major
    version suffix (`/v2`) and a gopkg.in version (`yaml.v3`) are skipped.
    """
    segments = import_path.split("/")
    last = segments[-1]
    if _MAJOR_VERSION.fullmatch(last) and len(segments) > 1:
        last = segments[-2]
    return last.split(".", 1)[0]


def imports(root: TSNode) -> Iterator[tuple[str, str]]:
    """(name, import path) per import spec; the name is the default one when none is written."""
    for declaration in root.children:
        if declaration.type != "import_declaration":
            continue
        for spec in _import_specs(declaration):
            path = text(spec.child_by_field_name("path")).strip('"`')
            if path:
                name = text(spec.child_by_field_name("name")) or default_import_name(path)
                yield name, path


def _import_specs(declaration: TSNode) -> Iterator[TSNode]:
    for child in declaration.children:
        if child.type == "import_spec":
            yield child
        elif child.type == "import_spec_list":
            yield from (c for c in child.children if c.type == "import_spec")


def base_type(node: TSNode | None) -> TSNode | None:
    """The named type under pointers and type arguments: `*Box[T]` -> `Box`."""
    while node is not None and node.type in ("pointer_type", "generic_type"):
        node = (
            node.child_by_field_name("type")
            if node.type == "generic_type"
            else node.named_children[0]
        )
    return node


def type_reference(node: TSNode | None) -> str:
    """A type as written, pointers and type arguments dropped: `Base`, `pkg.Other`; else ''."""
    named = base_type(node)
    if named is None or named.type not in ("type_identifier", "qualified_type"):
        return ""
    return text(named)


def type_fqn(node: TSNode | None, scope: str, import_map: Mapping[str, str]) -> str | None:
    """The FQN of a named type: same-package types take the scope, imported ones the path."""
    named = base_type(node)
    if named is None:
        return None
    if named.type == "type_identifier":
        name = text(named)
        return None if name in _PREDECLARED else f"{scope}.{name}"
    if named.type == "qualified_type":
        package = text(named.child_by_field_name("package"))
        name = text(named.child_by_field_name("name"))
        return f"{import_map.get(package, package)}.{name}"
    return None


def selector_chain(node: TSNode) -> list[str]:
    """`a.b.c` as ['a', 'b', 'c'] when it is identifiers and selectors only; else []."""
    if node.type == "identifier":
        return [text(node)]
    if node.type != "selector_expression":
        return []
    head = selector_chain(node.child_by_field_name("operand") or node)
    field = text(node.child_by_field_name("field"))
    return [*head, field] if head and field else []


def call_name(function: TSNode, receiver: str | None) -> str:
    """The raw name of a call: `helper`, `srv.Run`; the receiver reads as `self` (H6)."""
    chain = selector_chain(function)
    if not chain:
        return ""
    if receiver and len(chain) > 1 and chain[0] == receiver:
        chain[0] = "self"
    return ".".join(chain)


def literal_type(value: TSNode) -> TSNode | None:
    """The type of `T{}` or `&T{}`; None for any other value."""
    if value.type == "unary_expression" and text(value.child_by_field_name("operator")) == "&":
        operand = value.child_by_field_name("operand")
        value = operand if operand is not None else value
    if value.type == "composite_literal":
        return value.child_by_field_name("type")
    return None


def declared_names(node: TSNode) -> list[str]:
    """The names a parameter or var spec declares: `a, b T` -> ['a', 'b']."""
    return [text(c) for c in node.children_by_field_name("name")]
