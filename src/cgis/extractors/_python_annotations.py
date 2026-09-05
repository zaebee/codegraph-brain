"""Type-name collection from annotation subtrees (spec D9).

`clean_python_type_string` in `_python_types.py` answers "what type is this
variable" — it reduces `list[Node]` to `list`, which is correct for
`local_types` (the receiver of `x.append()` really is a list). A reference edge
asks the other question: which types does this annotation *name*. Measured on
HEAD, the difference is 330 of 577 internal-class references.
"""

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from tree_sitter import Node as BaseNode

_STRING_PARSER = Parser(Language(tspython.language()))

# Mirrors `TypeResolver._GENERIC_WRAPPERS` in `_python_types.py` (kept as a
# separate constant rather than importing a private class attribute across
# modules): these typing constructs are never themselves a type reference.
# `Annotated[T, ...]` additionally carries non-type metadata (typically a
# FastAPI `Depends(...)` call) in its arguments after the first — that
# metadata is walked separately as a CALLS/DEPENDS_ON edge via the call-node
# path (see `FunctionHandler.process_call_node`), so only the first argument
# is a type name here (#194).
_WRAPPER_NAMES: frozenset[str] = frozenset({"Optional", "Union", "Annotated"})


def collect_type_names(annotation_node: BaseNode, code_bytes: bytes) -> list[str]:
    """Return every type name mentioned in an annotation, deduplicated, in source order.

    Descends through generics, unions and nested subscripts, so `dict[str,
    list[Edge]]` yields the container and every argument. A dotted reference is
    kept whole (`models.BaseModel`) so the resolver can use its import map. A
    string annotation is re-parsed and its contents collected.
    """
    names: list[str] = []
    _collect(annotation_node, code_bytes, names)
    # dict.fromkeys de-duplicates while preserving first-seen order (guaranteed
    # since 3.7), which a set would lose — source order is part of the contract.
    return list(dict.fromkeys(names))


def _collect(node: BaseNode, code_bytes: bytes, acc: list[str]) -> None:
    """Walk an annotation subtree, appending each type name found to acc."""
    if node.type == "call":
        return  # a call expression (e.g. Depends(...)) names no type
    if node.type == "string":
        _collect_from_string(node, code_bytes, acc)
        return
    if node.type in ("identifier", "none", "attribute"):
        acc.append(code_bytes[node.start_byte : node.end_byte].decode("utf-8"))
        return  # a bare or dotted name is one reference; nothing further to descend into
    if node.type == "generic_type" and len(node.named_children) == 2:
        # `Base[args]` with a bare identifier base (e.g. `Optional[X]`) — tree-sitter
        # wraps each argument in its own `type` node inside `type_parameter`.
        base, type_parameter = node.named_children
        _collect_subscript(base, type_parameter.named_children, code_bytes, acc)
        return
    if node.type == "subscript" and len(node.named_children) >= 2:
        # `Base[args]` with a module-qualified base (e.g. `typing.Optional[X]`) — a
        # dotted base parses as a plain `subscript`, not `generic_type`, and its
        # arguments are unwrapped children rather than `type`-wrapped ones.
        base, *args = node.named_children
        _collect_subscript(base, args, code_bytes, acc)
        return
    for child in node.named_children:
        _collect(child, code_bytes, acc)


def _collect_subscript(
    base: BaseNode, args: list[BaseNode], code_bytes: bytes, acc: list[str]
) -> None:
    """Collect a `Base[args...]` subscript, unwrapping Optional/Union/Annotated (#194).

    `base` may be a bare identifier (`Optional[X]`) or a module-qualified attribute
    (`typing.Optional[X]`); the wrapper check strips the module prefix first —
    `base_name.rsplit(".", 1)[-1]` — mirroring `TypeResolver.clean_python_type_string`'s
    `split(".")[-1]`, which unwraps both forms identically for `local_types`.

    A wrapper's own name (`Optional`, `Union`, `Annotated`, qualified or not) is never a
    type reference, so it is skipped rather than collected. `Annotated[T, ...]`
    additionally only names `T` — the arguments after it are metadata, not types. Any
    other generic (`list[Node]`, `dict[str, Edge]`, `models.Registry[V]`) keeps its full
    base name and every argument.
    """
    base_name = code_bytes[base.start_byte : base.end_byte].decode("utf-8")
    wrapper = base_name.rsplit(".", maxsplit=1)[-1]
    if wrapper not in _WRAPPER_NAMES:
        acc.append(base_name)
        for arg in args:
            _collect(arg, code_bytes, acc)
        return
    relevant_args = args[:1] if wrapper == "Annotated" else args
    for arg in relevant_args:
        _collect(arg, code_bytes, acc)


def _string_body(node: BaseNode, code_bytes: bytes) -> str:
    """Return a string literal's contents, without its quotes or any prefix.

    tree-sitter gives the literal a `string_content` child, so the body is read
    directly rather than by stripping quote characters. Stripping cannot see a
    prefix — `r"Node"` strips to `r"Node`, which then only resolves because the
    parser recovers from the malformed re-parse. Reading the child makes the
    prefixed, triple-quoted and plain forms one case instead of three.
    """
    for child in node.named_children:
        if child.type == "string_content":
            return code_bytes[child.start_byte : child.end_byte].decode("utf-8")
    return ""


def _collect_from_string(node: BaseNode, code_bytes: bytes, acc: list[str]) -> None:
    """Re-parse a string annotation (PEP 563 style) and collect the names inside it."""
    raw = _string_body(node, code_bytes)
    if not raw:
        return
    inner_bytes = f"x: {raw}".encode()
    root = _STRING_PARSER.parse(inner_bytes).root_node
    # tree-sitter's error recovery has always synthesised these children for every
    # malformed input tried, so this guard is not known to be reachable — it states
    # the invariant rather than relying on that recovery behaviour holding.
    if not root.named_children or not root.named_children[0].named_children:
        return
    assignment = root.named_children[0].named_children[0]
    type_node = assignment.child_by_field_name("type")
    if type_node is not None:
        _collect(type_node, inner_bytes, acc)
