"""Pure AST naming and path helpers shared by the Python extractor handlers.

This is a leaf module: it imports only tree-sitter types and holds no state.
Every function is pure (its output depends solely on its arguments), which lets
the extractor's handler collaborators depend on it without import cycles.
"""

from tree_sitter import Node as BaseNode

PYTHON_LANG = "python"


def resolve_relative_module(module_fqn: str, leading_dots: int, relative_path: str) -> str:
    """Resolve a relative import to an absolute FQN.

    Args:
        module_fqn: FQN of the current file, e.g. "src.cgis.extractors.python_extractor"
        leading_dots: number of leading dots in the import statement
        relative_path: module path after the dots, e.g. "core" for "from ..core import x"

    Returns "src.cgis.core" for leading_dots=2, relative_path="core" in the example above.
    """
    segments = module_fqn.split(".")
    trim = min(leading_dots, len(segments))
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
