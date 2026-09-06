"""Pure AST naming and path helpers shared by the Python extractor handlers.

This is a leaf module: it imports only tree-sitter types and holds no state.
Every function is pure (its output depends solely on its arguments), which lets
the extractor's handler collaborators depend on it without import cycles.
"""

from tree_sitter import Node as BaseNode

PYTHON_LANG = "python"


def resolve_relative_module(
    module_fqn: str, leading_dots: int, relative_path: str, is_package: bool = False
) -> str:
    """Resolve a relative import to an absolute FQN.

    Args:
        module_fqn: FQN of the current file, e.g. "src.cgis.extractors.python_extractor"
        leading_dots: number of leading dots in the import statement
        relative_path: module path after the dots, e.g. "core" for "from ..core import x"
        is_package: True when the importing file is a package ``__init__.py``. Its
            FQN already had the ``/__init__`` suffix stripped, so it IS the
            package — one leading dot refers to the package itself and must trim
            one fewer segment than a regular module would.

    Returns "src.cgis.core" for leading_dots=2, relative_path="core" in the example above.
    """
    segments = module_fqn.split(".")
    effective_dots = leading_dots - 1 if is_package and leading_dots > 0 else leading_dots
    trim = min(effective_dots, len(segments))
    if trim == 0:
        # leading_dots == 0: not a relative import — return the module unchanged.
        # (Guards against `segments[:-0]` == `[]`, which would drop the whole FQN.)
        base = module_fqn
    elif trim == len(segments):
        base = ""
    else:
        base = ".".join(segments[:-trim])
    if relative_path:
        return f"{base}.{relative_path}" if base else relative_path
    return base


def file_path_to_module_fqn(file_path: str, source_root: str | None = None) -> str:
    """Convert a file path to a dot-separated module namespace.

    Examples:
        src/cgis/pipeline.py  -> src.cgis.pipeline
        src/cgis/__init__.py  -> src.cgis
        /abs/path/mod.py      -> abs.path.mod
        C:\\path\\to\\mod.py  -> path.to.mod

        With source_root="src":
        src/cgis/pipeline.py  -> cgis.pipeline
    """
    clean = file_path
    # Strip Windows drive letter (e.g. "C:") before normalising slashes
    if len(clean) >= 2 and clean[1] == ":" and clean[0].isalpha():
        clean = clean[2:]
    clean = clean.replace("\\", "/").lstrip("/")
    if source_root:
        # removeprefix("./") so CI-style roots ("cgis ingest ./src") strip too
        sr = source_root.replace("\\", "/").removeprefix("./").strip("/") + "/"
        if clean.startswith(sr):
            clean = clean[len(sr) :]
    if clean.endswith(".py"):
        clean = clean[:-3]
    if clean.endswith("/__init__"):
        clean = clean[:-9]
    return clean.replace("/", ".")


def extract_node_name(node: BaseNode | None, code_bytes: bytes) -> str:
    """Extract node name from name node using byte slicing."""
    if node:
        start, end = node.start_byte, node.end_byte
        return code_bytes[start:end].decode("utf8", errors="replace")
    return "unknown"


def get_identifier(node: BaseNode, code_bytes: bytes) -> str:
    """Extract name from AST node using byte slicing."""
    if node.type == "identifier":
        start, end = node.start_byte, node.end_byte
        return code_bytes[start:end].decode("utf8", errors="replace")
    if node.type == "parenthesized_expression":
        for child in node.children:
            if child.type not in ("(", ")", "comment"):
                return get_identifier(child, code_bytes)
    if node.type in ("attribute", "call", "subscript"):
        return extract_nested_name(node, code_bytes)
    return "unknown"


def extract_object_attr_name(node: BaseNode, code_bytes: bytes) -> str:
    """Extract identifier from object/attribute nodes."""
    obj_node = node.child_by_field_name("object")
    attr_node = node.child_by_field_name("attribute")
    if obj_node and attr_node:
        obj_id = get_identifier(obj_node, code_bytes)
        attr_id = get_identifier(attr_node, code_bytes)
        defined = obj_id != "unknown" and attr_id != "unknown"
        return f"{obj_id}.{attr_id}" if defined else "unknown"
    if attr_node:
        return get_identifier(attr_node, code_bytes)
    return "unknown"


def extract_nested_name(node: BaseNode, code_bytes: bytes) -> str:
    """Extract nested identifier from attribute/call/subscript nodes."""
    if node.type == "attribute":
        return extract_object_attr_name(node, code_bytes)
    if node.type == "call":
        func_node = node.child_by_field_name("function")
        if func_node:
            return get_identifier(func_node, code_bytes)
    elif node.type == "subscript":
        value_node = node.child_by_field_name("value")
        if value_node:
            return get_identifier(value_node, code_bytes)
    return "unknown"


def get_fqn_prefix(node: BaseNode, code_bytes: bytes) -> str | None:
    """Traverse up to find class and function names, returning them joined by dots."""
    parts = []
    curr = node.parent
    extract_types = ("class_definition", "function_definition", "async_function_definition")
    while curr:
        if curr.type in extract_types:
            name_node = curr.child_by_field_name("name")
            parts.append(extract_node_name(name_node, code_bytes))
        curr = curr.parent
    return ".".join(reversed(parts)) if parts else None


def enclosing_class_fqn(
    node: BaseNode, code_bytes: bytes, file_path: str, source_root: str | None
) -> str | None:
    """Return the FQN of the nearest enclosing class, or None outside any class.

    Collects every enclosing class *and* function definition (mirroring
    `get_fqn_prefix`), then truncates the chain at the last class in it. This
    reproduces `get_id`'s prefix construction exactly, so the returned FQN
    equals the CLASS node's `id` — including when a class is itself defined
    inside a method (e.g. `Outer.m.Local`), which a scheme that only ever
    collected class names would collapse onto an unrelated same-named class
    defined directly in `Outer`. The key in `self_types` must equal the CLASS
    node's own `id`, or the map would never be found by anything looking it
    up from the node.
    """
    curr = node.parent
    chain: list[tuple[str, bool]] = []
    while curr:
        if curr.type in ("class_definition", "function_definition", "async_function_definition"):
            chain.append(
                (
                    extract_node_name(curr.child_by_field_name("name"), code_bytes),
                    curr.type == "class_definition",
                )
            )
        curr = curr.parent
    chain.reverse()
    last_class = max((i for i, (_, is_cls) in enumerate(chain) if is_cls), default=None)
    if last_class is None:
        return None
    path = ".".join(name for name, _ in chain[: last_class + 1])
    module = file_path_to_module_fqn(file_path, source_root)
    return f"{module}.{path}" if module else path


def assigned_attr_name(left: BaseNode, code_bytes: bytes) -> str | None:
    """Return the attribute name being assigned: `self.x` -> "x", `x: T` -> "x".

    Returns None for anything else — a subscript target, a tuple unpack, or an
    attribute on a receiver other than `self`.
    """
    if left.type == "identifier":
        return code_bytes[left.start_byte : left.end_byte].decode("utf-8")
    if left.type != "attribute":
        return None
    obj = left.child_by_field_name("object")
    attr = left.child_by_field_name("attribute")
    if obj is None or attr is None:
        return None
    if code_bytes[obj.start_byte : obj.end_byte].decode("utf-8") != "self":
        return None
    return code_bytes[attr.start_byte : attr.end_byte].decode("utf-8")


def get_id(node: BaseNode, code_bytes: bytes, file_path: str, source_root: str | None) -> str:
    """Generate a fully qualified function/method ID including class/function context."""
    name = node.child_by_field_name("name")
    node_name = extract_node_name(name, code_bytes)
    prefix = get_fqn_prefix(node, code_bytes)
    module = file_path_to_module_fqn(file_path, source_root)
    if not module:
        return f"{prefix}.{node_name}" if prefix else node_name
    return f"{module}.{prefix}.{node_name}" if prefix else f"{module}.{node_name}"


def is_method(node: BaseNode) -> bool:
    """Check if a function node is a method (defined inside a class)."""
    curr = node.parent
    while curr:
        if curr.type == "class_definition":
            return True
        if curr.type in ("function_definition", "async_function_definition"):
            return False
        curr = curr.parent
    return False


def is_module_level_assignment(
    node: BaseNode, code_bytes: bytes, current_func_node: object | None
) -> bool:
    """True for assignments at true module level (not function, not class body)."""
    return (
        node.type == "assignment"
        and current_func_node is None
        and get_fqn_prefix(node, code_bytes) is None
    )


def get_decorator_name(decorator_node: BaseNode, code_bytes: bytes) -> str | None:
    """Return the decorator's callable name from a single decorator node, or None.

    Skips the leading '@' token and any comments; passes the first real expression
    node to get_identifier, which handles identifier / attribute / call / PEP-614
    parenthesized expressions.
    """
    for inner in decorator_node.children:
        if inner.type not in ("@", "comment"):
            name = get_identifier(inner, code_bytes)
            return name if name != "unknown" else None
    return None


def extract_decorator_names(node: BaseNode, code_bytes: bytes) -> list[str]:
    """Extract decorator names from a decorated_definition node."""
    names: list[str] = []
    for child in node.children:
        if child.type == "decorator":
            name = get_decorator_name(child, code_bytes)
            if name:
                names.append(name)
    return names


# Nodes that only group other nodes: a tuple, a list, an unpacking target. An
# identifier inside one is used — or bound — by whatever encloses the group, so
# the test climbs out of them before deciding. This is what makes
# `for a, (b, Widget) in xs` a binding and `f((a, Widget))` a use without
# enumerating either shape.
_GROUPING: frozenset[str] = frozenset(
    {
        "tuple",
        "list",
        "set",
        "dictionary",
        "pair",
        "parenthesized_expression",
        "list_splat",
        "dictionary_splat",
        "pattern_list",
        "tuple_pattern",
        "list_pattern",
        "list_splat_pattern",
        "dictionary_splat_pattern",
    }
)

# The parent field an identifier sits in when it is NOT a use of that name.
_NOT_A_LOAD: frozenset[tuple[str, str]] = frozenset(
    {
        ("call", "function"),  # a construction; CALLS already carries it
        ("attribute", "attribute"),  # the member half of `Type.MEMBER`
        ("keyword_argument", "name"),  # the label, not the value
        ("assignment", "left"),  # a store, not a use
        ("augmented_assignment", "left"),
        ("default_parameter", "name"),  # a parameter, not a reference
        ("typed_parameter", "name"),
        ("typed_default_parameter", "name"),
        ("function_definition", "name"),
        ("class_definition", "name"),
        ("for_statement", "left"),  # the loop variable
        ("for_in_clause", "left"),  # the same, in a comprehension
        ("named_expression", "name"),  # the walrus binds its left side
    }
)

# Parents whose identifier children are all bindings, module paths, or already
# covered by another edge.
_BINDING_PARENTS: frozenset[str] = frozenset(
    {
        "aliased_import",
        "parameters",
        "lambda_parameters",
        "global_statement",
        "nonlocal_statement",
        "delete_statement",
        "as_pattern_target",  # the name `with x as N` / `except E as N` binds
        # An annotation is already a `rawdep_` edge from the annotation path, and
        # the same node type also spells a PEP 695 alias name (`type N = ...`)
        # and a type parameter (`def f[N]`), both of which bind. Excluding it
        # here removes a duplicate and two bindings in one line.
        "type",
    }
)

# Two parents hold a use and a binding side by side, told apart only by order.
_LOAD_IS_FIRST_CHILD: frozenset[str] = frozenset({"as_pattern"})  # `except Widget as e`
_LOAD_IS_NOT_FIRST_CHILD: frozenset[str] = frozenset({"keyword_pattern"})  # `case O(label=W)`


def _field_name(parent: BaseNode, child: BaseNode) -> str | None:
    """The field name the parent uses for this child, if it names it at all."""
    for i in range(parent.child_count):
        candidate = parent.child(i)
        if candidate is not None and candidate.id == child.id:
            return parent.field_name_for_child(i)
    return None


def is_name_load(node: BaseNode) -> bool:
    """Does this identifier *use* the name it spells, rather than bind or qualify it?

    The question D10 turns on: `add_middleware(Widget)`, `except Widget:`,
    `Widget.SIZE`, `[("w", Widget)]` and `case Widget():` all name a class
    without constructing it, and a class only ever used that way has no edge at
    all — which is what made the #415 orphan query wrong on a third of what it
    reported for every application codebase measured.

    Excluded are the positions that bind the name (an assignment or loop target,
    a parameter, a walrus, a `with`/`except`/`case` alias, a definition's own
    name) and those that already carry an edge of their own: a call's `function`
    field is CALLS, and an annotation is the annotation path's `rawdep_` edge.
    The member half of an attribute is excluded too — `TOP_UP` in
    `TransactionType.TOP_UP` is a field lookup on whatever the object turns out
    to be, not a class anyone imported.

    Both reviewers of the original version found the same hole: a denylist over
    a grammar rots. `test_name_load_positions` pins every position this docstring
    claims, so a grammar the list has not met fails there rather than silently.
    """
    target = node
    parent = target.parent
    while parent is not None and parent.type in _GROUPING:
        target, parent = parent, parent.parent
    if parent is None or parent.type in _BINDING_PARENTS:
        return False
    if parent.type in _LOAD_IS_FIRST_CHILD:
        return bool(parent.named_children) and parent.named_children[0].id == target.id
    if parent.type in _LOAD_IS_NOT_FIRST_CHILD:
        return bool(parent.named_children) and parent.named_children[0].id != target.id
    if parent.type == "dotted_name":
        # `case Widget:` binds a fresh name; `case Widget():` and `case Widget.A:`
        # name the class. All three spell it through a dotted_name, and only the
        # bare one-segment form directly under a case_pattern is the capture.
        grandparent = parent.parent
        return not (
            grandparent is not None
            and grandparent.type == "case_pattern"
            and len(parent.named_children) == 1
        )
    return (parent.type, _field_name(parent, target) or "") not in _NOT_A_LOAD
