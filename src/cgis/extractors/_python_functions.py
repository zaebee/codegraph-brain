"""Function/method-definition handling for the Python extractor.

Emits FUNCTION/METHOD nodes plus their CALLS, DECLARES/CONTAINS, DEPENDS_ON
edges, and collects local type information (assignments and typed parameters)
used downstream for call resolution. It also builds the `self_types` map for
the enclosing class (spec D1) and emits `raw_dep:` annotation candidates from
parameter, return, and `AnnAssign` positions, which the resolver turns into
REFERENCES edges for internal classes (spec D4/D9).
"""

from collections.abc import Callable
from typing import Any

from tree_sitter import Node as BaseNode

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors._python_annotations import collect_type_names
from cgis.extractors._python_ast import (
    PYTHON_LANG,
    assigned_attr_name,
    extract_node_name,
    get_id,
    get_identifier,
    is_method,
)
from cgis.extractors._python_types import TypeResolver


class FunctionHandler:
    """Extracts function/method nodes, call edges and local type metadata.

    Sits at 9 methods. `_GOD_OBJECT_MIN_METHODS` (query/analysis/analyzer.py)
    is 10, so the next method added here trips the self-parsing God-Object
    gate — put new standalone logic in a module-level function instead (see
    `emit_annotation_edges` below for the pattern).
    """

    _DI_CALL_NAMES: frozenset[str] = frozenset({"Depends", "Security"})

    def __init__(
        self,
        pick_source_root: Callable[[str], str | None],
        type_resolver: TypeResolver,
    ) -> None:
        """Store the source-root picker and the shared type resolver."""
        self._pick_source_root = pick_source_root
        self._types = type_resolver

    def process_function_node(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        module_fqn: str,
        decorators: list[str] | None = None,
    ) -> Node:
        """Process function or method definition node."""
        child = node.child_by_field_name("name")
        node_id = get_id(node, code_bytes, file_path, self._pick_source_root(file_path))
        node_name = extract_node_name(child, code_bytes)
        node_type = NodeType.METHOD if is_method(node) else NodeType.FUNCTION

        metadata: dict[str, Any] = {}
        if decorators:
            metadata["decorators"] = decorators
        if decorators and any(
            d == "abstractmethod" or d.endswith(".abstractmethod") for d in decorators
        ):
            metadata["is_abstract"] = True

        func_node = Node(
            id=node_id,
            type=node_type,
            name=node_name,
            file_path=file_path,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            language=PYTHON_LANG,
            metadata=metadata,
        )
        nodes.append(func_node)

        edges.extend(
            Edge(
                id=f"{node_id}:decorator:{i}:{deco_name}",
                type=EdgeType.CALLS,
                source=node_id,
                target=f"raw_call:{deco_name}",
                confidence=0.5,
                file_path=file_path,
            )
            for i, deco_name in enumerate(decorators or [])
        )

        parts = node_id.rsplit(".", maxsplit=1)
        parent_fqn = parts[0] if len(parts) > 1 else module_fqn
        edge_type = EdgeType.DECLARES if node_type == NodeType.METHOD else EdgeType.CONTAINS
        edges.append(
            Edge(
                id=f"{parent_fqn}:structural:{node_id}",
                type=edge_type,
                source=parent_fqn,
                target=node_id,
                confidence=1.0,
                file_path=file_path,
            )
        )
        return func_node

    def process_call_node(
        self, node: BaseNode, code_bytes: bytes, file_path: str, source_id: str, edges: list[Edge]
    ) -> None:
        """
        Finds call expressions node.
        """
        child = node.child_by_field_name("function")
        edge_id = f"{file_path}:edge_{node.start_byte}_{node.end_byte}"
        if child:
            call_name = get_identifier(child, code_bytes)
            if call_name == "unknown":
                return
            target_id = f"raw_call:{call_name}"

            edges.append(
                Edge(
                    id=edge_id,
                    type=EdgeType.CALLS,
                    source=source_id,
                    target=target_id,
                    confidence=0.5,
                    context=f"Call to {call_name}",
                    file_path=file_path,
                    line_number=node.start_point.row + 1,
                )
            )
            if call_name in self._DI_CALL_NAMES:
                provider = self._di_provider_name(node, code_bytes)
                if provider:
                    edges.append(
                        Edge(
                            id=f"{file_path}:dep_{node.start_byte}_{node.end_byte}",
                            type=EdgeType.DEPENDS_ON,
                            source=source_id,
                            target=f"raw_call:{provider}",
                            confidence=0.5,
                            context=f"DI dependency on {provider}",
                            file_path=file_path,
                            line_number=node.start_point.row + 1,
                        )
                    )

    def _di_provider_name(self, call_node: BaseNode, code_bytes: bytes) -> str | None:
        """Return the first positional argument's identifier/dotted name, or None.

        None for argless calls, keyword-only calls, and non-name arguments
        (lambdas, calls, subscripts) — those emit no DEPENDS_ON edge (spec §3.2a/b).

        Note: ``child_by_field_name("arguments")`` returns a truthy
        ``argument_list`` node even for ``Depends()`` (argless), so the
        ``if not args`` guard here only covers a *missing* arguments field
        (should not occur in practice).  The argless case — where
        ``args.named_children`` is empty — is handled by the loop falling
        through to the final ``return None``.
        """
        args = call_node.child_by_field_name("arguments")
        if not args:
            return None
        for child in args.named_children:
            if child.type == "keyword_argument":
                continue
            if child.type in ("identifier", "attribute"):
                name = get_identifier(child, code_bytes)
                return name if name != "unknown" else None
            return None
        return None

    def _find_di_calls(self, node: BaseNode, code_bytes: bytes) -> list[BaseNode]:
        """Return all call nodes in the subtree whose callee is a DI name (spec §3.2a)."""
        found: list[BaseNode] = []
        stack = [node]
        while stack:
            curr = stack.pop()
            if curr.type == "call":
                fn = curr.child_by_field_name("function")
                if fn and get_identifier(fn, code_bytes) in self._DI_CALL_NAMES:
                    found.append(curr)
            stack.extend(curr.children)
        return found

    def process_module_assignment(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        module_fqn: str,
    ) -> None:
        """Emit a VARIABLE alias node + DEPENDS_ON edges for module-level DI assignments.

        Only fires for `Name = <RHS containing Depends/Security>` with a plain
        identifier LHS at true module level (class bodies excluded by the
        caller). Plain constants never reach the node list (spec §3.2a).
        """
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if not left or not right or left.type != "identifier":
            return
        di_calls = self._find_di_calls(right, code_bytes)
        if not di_calls:
            return
        name = get_identifier(left, code_bytes)
        if name == "unknown":
            return
        alias_id = f"{module_fqn}.{name}" if module_fqn else name
        nodes.append(
            Node(
                id=alias_id,
                type=NodeType.VARIABLE,
                name=name,
                file_path=file_path,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                language=PYTHON_LANG,
            )
        )
        for call in di_calls:
            provider = self._di_provider_name(call, code_bytes)
            if not provider:
                continue
            edges.append(
                Edge(
                    id=f"{file_path}:dep_{call.start_byte}_{call.end_byte}",
                    type=EdgeType.DEPENDS_ON,
                    source=alias_id,
                    target=f"raw_call:{provider}",
                    confidence=0.5,
                    context=f"DI alias for {provider}",
                    file_path=file_path,
                    line_number=node.start_point.row + 1,
                )
            )

    def collect_assignment_type(
        self,
        node: BaseNode,
        code_bytes: bytes,
        import_map: dict[str, str] | None,
        func_node: Node,
        acc: dict[str, dict[str, str]],
    ) -> None:
        """Populate acc with var→FQN for `var = ClassName(...)` assignments."""
        left_node = node.child_by_field_name("left")
        right_node = node.child_by_field_name("right")
        if not left_node or not right_node or right_node.type != "call":
            return
        var_name = get_identifier(left_node, code_bytes)
        if var_name == "unknown":
            return
        func_call_node = right_node.child_by_field_name("function")
        if not func_call_node:
            return
        class_name = get_identifier(func_call_node, code_bytes)
        if class_name == "unknown":
            return
        if "." in class_name:
            # Module-qualified constructor (e.g. models.Store()): keep only if prefix
            # is a known import alias. Otherwise it's a method-call result — skip.
            module_part, _, _ = class_name.partition(".")
            if not import_map or module_part not in import_map:
                return
        acc.setdefault(func_node.id, {})[var_name] = self._types.resolve_type_fqn(
            class_name, import_map, func_node.file_path
        )

    def collect_param_type(
        self,
        node: BaseNode,
        code_bytes: bytes,
        import_map: dict[str, str] | None,
        func_node: Node,
        acc: dict[str, dict[str, str]],
        edges: list[Edge],
    ) -> None:
        """Populate acc with param→FQN for typed parameter annotations.

        Also emits one speculative `raw_dep:` DEPENDS_ON candidate per type name
        found in the parameter annotation (spec D9); the resolver keeps a name
        only when it resolves to a DI alias (VARIABLE node) or an internal class
        and drops the rest (spec §3.2c). Annotation-edge emission is independent
        of the `local_types` population below it: a splat parameter
        (`*args: T`, `**kw: T`) has no plain identifier for `local_types` to key
        on, but its annotation still names a real type, so the two questions
        must not share one early return.
        """
        if not node.named_children:
            return
        name_node = node.named_children[0]
        type_node = node.child_by_field_name("type")
        if not type_node:
            return
        emit_annotation_edges(type_node, code_bytes, func_node.id, func_node.file_path, edges)
        var_name = get_identifier(name_node, code_bytes)
        if var_name == "unknown":
            return
        # Slice raw bytes to capture union/generic types like `A | None` or `list[X]`
        raw_type = (
            code_bytes[type_node.start_byte : type_node.end_byte].decode("utf-8").strip("\"'")
        )
        clean_type = self._types.clean_python_type_string(raw_type)
        if not clean_type:
            return
        acc.setdefault(func_node.id, {})[var_name] = self._types.resolve_type_fqn(
            clean_type, import_map, func_node.file_path
        )

    def collect_self_type(
        self,
        node: BaseNode,
        code_bytes: bytes,
        import_map: dict[str, str] | None,
        class_fqn: str,
        file_path: str,
        init_param_types: dict[str, str],
        acc: dict[str, dict[str, str]],
        in_class_body: bool,
    ) -> None:
        """Populate acc with attr→FQN for one assignment inside a class (spec D1).

        Handles the four annotated shapes: a class-body annotation, an annotated
        attribute assignment, an attribute assigned from a typed `__init__`
        parameter, and an attribute assigned a constructor call. An attribute
        with no annotation anywhere is left out rather than guessed at. The two
        shape-specific lookups are module-level helpers rather than methods —
        they take the type resolver as a parameter, so the dispatcher here stays
        simple and the class doesn't grow a method for each RHS shape.

        `in_class_body` (true only when this assignment has no enclosing
        function) distinguishes the class-body-annotation shape (`x: T` in the
        class body) from a same-shaped but unrelated case: a locally annotated
        variable inside a method (`y: T = ...`), which `assigned_attr_name`
        cannot tell apart from a class attribute on its own since both have a
        bare-identifier LHS. Without this check a method-local annotated
        variable would be misfiled as an attribute of the enclosing class.
        """
        left = node.child_by_field_name("left")
        if left is None:
            return
        attr_name = assigned_attr_name(left, code_bytes)
        if attr_name is None:
            return
        if left.type == "identifier" and not in_class_body:
            return

        type_node = node.child_by_field_name("type")
        if type_node is not None:
            resolved = _resolve_self_type_annotation(
                type_node, code_bytes, import_map, file_path, self._types
            )
        else:
            right = node.child_by_field_name("right")
            resolved = (
                _resolve_self_type_from_rhs(
                    right, code_bytes, import_map, file_path, self._types, init_param_types
                )
                if right is not None
                else None
            )
        if resolved:
            acc.setdefault(class_fqn, {})[attr_name] = resolved


def emit_annotation_edges(
    type_node: BaseNode,
    code_bytes: bytes,
    source_fqn: str,
    file_path: str,
    edges: list[Edge],
) -> None:
    """Emit one speculative `raw_dep:` edge per type named in an annotation.

    The resolver keeps a name that resolves to a DI alias (as DEPENDS_ON) or
    to an internal class (as REFERENCES) and drops the rest, so emitting
    stdlib and third-party names here costs nothing downstream (spec D3/D4).

    Module-level, not a `FunctionHandler` method: it needs only
    `collect_type_names` and `Edge`, no instance state, and the repo's
    God-Object gate fires at 10 methods (`_GOD_OBJECT_MIN_METHODS`) —
    `FunctionHandler` is already at 9, so adding this as a method would trip
    the self-parsing gate (`test_god_object_baseline_not_exceeded`).
    """
    for name in collect_type_names(type_node, code_bytes):
        if name == "None":
            continue
        edges.append(
            Edge(
                id=f"{file_path}:rawdep_{type_node.start_byte}_{type_node.end_byte}_{name}",
                type=EdgeType.DEPENDS_ON,
                source=source_fqn,
                target=f"raw_dep:{name}",
                confidence=0.1,
                context=f"Annotation candidate {name}",
                file_path=file_path,
                line_number=type_node.start_point.row + 1,
            )
        )


def collect_return_annotation(
    node: BaseNode,
    code_bytes: bytes,
    func_node: Node,
    file_path: str,
    edges: list[Edge],
) -> None:
    """Emit annotation edges for a function's return type, if it has one.

    Module-level for the same reason as `emit_annotation_edges`: it would
    push `FunctionHandler` past the 10-method God-Object gate.
    """
    type_node = node.child_by_field_name("return_type")
    if type_node is None:
        return
    emit_annotation_edges(type_node, code_bytes, func_node.id, file_path, edges)


def _resolve_self_type_annotation(
    type_node: BaseNode,
    code_bytes: bytes,
    import_map: dict[str, str] | None,
    file_path: str,
    type_resolver: TypeResolver,
) -> str | None:
    """Resolve an explicit `: T` annotation (class-body `x: T` or `self.x: T = ...`)."""
    raw = code_bytes[type_node.start_byte : type_node.end_byte].decode("utf-8")
    clean = type_resolver.clean_python_type_string(raw.strip("\"'"))
    return type_resolver.resolve_type_fqn(clean, import_map, file_path) if clean else None


def _resolve_self_type_from_rhs(
    right: BaseNode,
    code_bytes: bytes,
    import_map: dict[str, str] | None,
    file_path: str,
    type_resolver: TypeResolver,
    init_param_types: dict[str, str],
) -> str | None:
    """Resolve an unannotated `self.x = <rhs>` via a typed `__init__` param or constructor call."""
    if right.type == "identifier":
        return init_param_types.get(get_identifier(right, code_bytes))
    if right.type == "call":
        func_node = right.child_by_field_name("function")
        if func_node is None:
            return None
        class_name = get_identifier(func_node, code_bytes)
        if class_name == "unknown":
            return None
        if "." in class_name:
            # Module-qualified constructor (e.g. client.SearchClient()): keep only
            # if the prefix is a known import alias, mirroring collect_assignment_type
            # above. Otherwise it is a method call on a local object — `factory.Build()`
            # is a result, not a construction, and guessing here would put the wrong
            # type in self_types for every builder-style attribute.
            module_part, _, _ = class_name.partition(".")
            if not import_map or module_part not in import_map:
                return None
        # Leading underscores are stripped before the capitalisation test: `_IdAllocator()`
        # is a construction by every Python convention, and this repo has such classes.
        # A name that is only underscores yields "" here, which is correctly not upper.
        if not class_name.rpartition(".")[-1].lstrip("_")[:1].isupper():
            return None
        return type_resolver.resolve_type_fqn(class_name, import_map, file_path)
    return None
