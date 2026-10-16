"""Readings of tree-sitter-go syntax the Go extractor needs — no node or edge building."""

import re
from collections.abc import Iterator, Mapping

from tree_sitter import Node as TSNode

#: Go's predeclared types: never a type of ours to look methods up on.
PREDECLARED = frozenset(
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
BUILTINS = _BUILTIN_FUNCTIONS | PREDECLARED
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
    """The named type under pointers and type arguments: `*Box[T]` -> `Box`.

    None for a pointer with nothing under it — code still being typed.
    """
    while node is not None and node.type in ("pointer_type", "generic_type"):
        if node.type == "generic_type":
            node = node.child_by_field_name("type")
        else:
            node = node.named_children[0] if node.named_children else None
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
        return None if name in PREDECLARED else f"{scope}.{name}"
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
    operand = node.child_by_field_name("operand")
    if operand is None:
        return []  # incomplete syntax; recursing on `node` itself never ended
    head = selector_chain(operand)
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


#: Marks a local type that is a call's result, for the Go type normalizer:
#: `go-returns:<i>:<callee>`, where the callee is an FQN, or `<marker>|<method>`
#: for a method called on another call's result (#608).
CALL_RESULT = "go-returns:"
#: A name bound to several known types in one function: `go-any:<t1>;<t2>…`. One
#: dict serves every block, so the resolver keeps the type only if all agree (#608).
ANY_OF = "go-any:"


def result_type_name(node: TSNode | None, scope: str, import_map: Mapping[str, str]) -> str:
    """A result's type: a named type's FQN, a predeclared type by name (`error`), else ''."""
    fqn = type_fqn(node, scope, import_map) or container_type(node, scope, import_map)
    if fqn:
        return fqn
    named = base_type(node)
    return text(named) if named is not None and text(named) in PREDECLARED else ""


def result_types(declaration: TSNode, scope: str, import_map: Mapping[str, str]) -> list[str]:
    """One entry per result of a function or method, in order (#608)."""
    result = declaration.child_by_field_name("result")
    if result is None:
        return []
    if result.type != "parameter_list":
        return [result_type_name(result, scope, import_map)]
    types: list[str] = []
    for item in result.named_children:
        if item.type == "parameter_declaration":
            name = result_type_name(item.child_by_field_name("type"), scope, import_map)
            types.extend([name] * max(1, len(declared_names(item))))
    return types


def arity(node: TSNode) -> list[int]:
    """`[parameters, results]` a function, method or interface method takes (#646).

    `a, b int` is two, `int, int` is two, `...T` is one; a result that is a bare
    type is one. A list, not a tuple: node metadata must read back equal from JSON.
    """
    return [
        _count(node.child_by_field_name("parameters")),
        _count(node.child_by_field_name("result")),
    ]


def _count(params: TSNode | None) -> int:
    """How many values a parameter or result list declares."""
    if params is None:
        return 0
    if params.type != "parameter_list":
        return 1  # a single unnamed result type: `func() error`
    return sum(
        max(1, len(declared_names(d)))
        for d in params.named_children
        if d.type in ("parameter_declaration", "variadic_parameter_declaration")
    )


#: A container's element type: `go-elem:<chan|seq|map>:<element>` for `chan T`,
#: `[]T` or `[N]T`, and `go-elem:map:<T>,<K>` for `map[K]T` (#643 B). A container
#: has no methods; the marker is there for the element a receive, an index or a
#: range takes from it.
ELEM = "go-elem:"
#: An element taken from a value: `go-take:<elem|key>:<the value's type>`. `elem`
#: is what a receive, an index or a range's second variable gives; `key` a range's
#: first: a channel's element, a map's key, nothing for a slice.
TAKE = "go-take:"
#: A field chain on a value: `go-field:<f1.f2>:<the value's type>`.
FIELD = "go-field:"
#: A package variable's declared type, by its node id: `go-global:<scope>.<name>`.
GLOBAL = "go-global:"
_CONTAINERS = {
    "channel_type": ("chan", "value"),
    "slice_type": ("seq", "element"),
    "array_type": ("seq", "element"),
    "map_type": ("map", "value"),
}


def container_type(node: TSNode | None, scope: str, import_map: Mapping[str, str]) -> str | None:
    """`go-elem:<kind>:<T>` for a channel, slice, array or map of a named type; else None.

    A map also names its key: `go-elem:map:<V>,<K>`, either side empty when unnamed.
    """
    if node is None or node.type not in _CONTAINERS:
        return None
    kind, field = _CONTAINERS[node.type]
    element = type_fqn(node.child_by_field_name(field), scope, import_map) or ""
    if kind == "map":
        key = type_fqn(node.child_by_field_name("key"), scope, import_map) or ""
        return f"{ELEM}map:{element},{key}" if element or key else None
    return f"{ELEM}{kind}:{element}" if element else None
