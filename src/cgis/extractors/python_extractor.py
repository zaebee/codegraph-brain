"""Implements Python Extractor."""

import re

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from tree_sitter import Node as BaseNode

from cgis.core.models import Edge, Node, NodeType
from cgis.extractors._python_ast import (
    enclosing_class_fqn,
    extract_decorator_names,
    is_module_level_assignment,
    is_name_load,
)
from cgis.extractors._python_ast import file_path_to_module_fqn as _file_path_to_module_fqn
from cgis.extractors._python_classes import ClassHandler
from cgis.extractors._python_functions import (
    FunctionHandler,
    collect_return_annotation,
    emit_annotation_edges,
    emit_name_reference_edges,
)
from cgis.extractors._python_imports import ImportHandler
from cgis.extractors._python_types import TypeResolver
from cgis.extractors.base import BaseExtractor


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
    return _file_path_to_module_fqn(file_path, source_root)


def _identifiers_outside_imports(root: BaseNode, code_bytes: bytes) -> set[str]:
    """Every identifier used in the module body, excluding import statements.

    The walker already refuses to recurse into import nodes, so the same rule is
    applied here: a name that appears *only* in an import is never used by this
    module.
    """
    used: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in ("import_statement", "import_from_statement"):
            continue
        if node.type == "identifier":
            # `o.thing` and `f(thing=1)` mention `thing` without using the imported
            # name — counting them would let an unrelated attribute or keyword
            # silence a real re-export of that name. Only the `attribute`/`name`
            # field is excluded; the object side of `o.thing` is a genuine use.
            parent = node.parent
            shadowed = parent is not None and (
                (parent.type == "attribute" and parent.child_by_field_name("attribute") == node)
                or (
                    parent.type == "keyword_argument" and parent.child_by_field_name("name") == node
                )
            )
            if not shadowed:
                used.add(code_bytes[node.start_byte : node.end_byte].decode("utf8"))
        elif node.type == "string" and node.parent is not None and node.parent.type == "type":
            # A forward-reference annotation — `store: "SQLiteStore | None"` — holds
            # no identifier nodes, so without this a TYPE_CHECKING import used only
            # in quoted annotations reads as unused. Restricted to annotation
            # strings (parent `type`): scanning every string would let a docstring
            # mentioning a name mask a real re-export.
            text = code_bytes[node.start_byte : node.end_byte].decode("utf8")
            used.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
        stack.extend(node.children)
    return used


def find_reexports(root: BaseNode, code_bytes: bytes, import_map: dict[str, str]) -> dict[str, str]:
    """Return the imports this module never uses — i.e. passes straight through (#182).

    A transparent re-export lets one module absorb another's import edges, so an
    N-way coupling can be linearised on paper without changing what actually
    depends on what. Naming them is the precondition for the IMPORTS census
    being able to look through them.

    "Imported but unused" is the whole test, and in a linted tree that is a
    precise proxy: ruff's F401 removes genuine dead imports, so what survives is
    deliberate — `X as X`, an `__all__` entry, or an explicit noqa.
    """
    used = _identifiers_outside_imports(root, code_bytes)
    return {local: target for local, target in import_map.items() if local not in used}


class PythonExtractor(BaseExtractor):
    """
    A concrete extractor for Python source code using tree-sitter.

    Acts as a thin orchestrator: the recursive AST walk dispatches each node to
    a focused collaborator (ImportHandler, FunctionHandler, ClassHandler,
    TypeResolver) which owns the actual node/edge extraction.
    """

    LANG: str = "python"

    def __init__(self, source_roots: list[str] | None = None) -> None:
        """Initialise the tree-sitter Python parser and the extraction handlers."""
        super().__init__(source_roots=source_roots)
        self._parser = Parser(Language(tspython.language()))
        self._imports = ImportHandler()
        self._types = TypeResolver(self._pick_source_root)
        self._functions = FunctionHandler(self._pick_source_root, self._types)
        self._classes = ClassHandler(self._pick_source_root)

    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """
        Extracts structural nodes and edges (Functions, Classes, Imports).
        """
        code_bytes = code.encode("utf8")
        tree = self._parser.parse(code_bytes)
        root_node: BaseNode = tree.root_node

        nodes: list[Node] = []
        edges: list[Edge] = []
        import_map: dict[str, str] = {}
        module_fqn = file_path_to_module_fqn(file_path, self._pick_source_root(file_path))
        local_types_acc: dict[str, dict[str, str]] = {}
        self_types_acc: dict[str, dict[str, str]] = {}
        star_imports: list[str] = []
        name_refs_acc: list[tuple[str, str, int]] = []

        self._walk(
            root_node,
            code_bytes,
            file_path,
            nodes,
            edges,
            import_map=import_map,
            module_fqn=module_fqn,
            local_types_acc=local_types_acc,
            self_types_acc=self_types_acc,
            star_imports=star_imports,
            name_refs_acc=name_refs_acc,
        )

        # A name reference is gated on the finished import map and on the classes
        # this file defines, neither of which is complete until the walk is over:
        # an import at the bottom of the module, or a class defined below its own
        # use, would otherwise be missed (spec D10).
        emit_name_reference_edges(
            name_refs_acc,
            set(import_map) | {n.name for n in nodes if n.type == NodeType.CLASS},
            file_path,
            edges,
        )

        # Apply accumulated local types from assignments and param annotations
        nodes_by_id = {n.id: i for i, n in enumerate(nodes)}
        for func_id, lt in local_types_acc.items():
            if func_id in nodes_by_id:
                i = nodes_by_id[func_id]
                nodes[i] = nodes[i].model_copy(
                    update={"metadata": {**nodes[i].metadata, "local_types": lt}}
                )
        # Apply accumulated attribute types to their class nodes (spec D1)
        for class_id, st in self_types_acc.items():
            if class_id in nodes_by_id:
                i = nodes_by_id[class_id]
                nodes[i] = nodes[i].model_copy(
                    update={"metadata": {**nodes[i].metadata, "self_types": st}}
                )

        reexports = find_reexports(root_node, code_bytes, import_map)

        file_node = Node(
            id=module_fqn,
            type=NodeType.FILE,
            name=file_path.rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1],
            file_path=file_path,
            start_line=1,
            end_line=root_node.end_point.row + 1,
            metadata={
                "import_map": import_map,
                "reexports": reexports,
                "star_imports": star_imports,
            },
        )
        nodes.insert(0, file_node)

        return nodes, edges

    def _handle_assignment(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        module_fqn: str,
        import_map: dict[str, str] | None,
        current_func_node: Node | None,
        local_types_acc: dict[str, dict[str, str]] | None,
        self_types_acc: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """Route one assignment: a local type hint, a module-level DI alias, or neither.

        Extracted so `_walk` stays under the complexity limit while the node-type
        dispatch above remains in one place. The original optimisation split out
        the *definition* branch instead, which cost a second `node.type` test on
        every definition; the branching that actually grew is here.

        `current_func_node` is tested before `is_module_level_assignment` rather
        than after: inside a function the assignment can only be a local hint,
        and the predicate would return False on that same condition — so the
        check skips the call instead of paying for it to say no.

        Also records attribute types onto the enclosing class's `self_types`
        accumulator (spec D1) — independent of, and in addition to, the
        function-local routing below, since a class-body annotation has no
        enclosing function at all.

        Also emits `raw_dep:` annotation edges (spec D4/D9) for an `AnnAssign`
        (`x: T` or `x: T = ...`), sourced from the enclosing function if there
        is one, else the enclosing class. A bare module-level annotation (no
        enclosing function or class) has no source FQN and emits nothing.
        """
        class_fqn = enclosing_class_fqn(
            node, code_bytes, file_path, self._pick_source_root(file_path)
        )
        if class_fqn is not None and self_types_acc is not None:
            init_param_types = (
                local_types_acc.get(f"{class_fqn}.__init__", {})
                if local_types_acc is not None
                else {}
            )
            self._functions.collect_self_type(
                node,
                code_bytes,
                import_map,
                class_fqn,
                file_path,
                init_param_types,
                self_types_acc,
                in_class_body=current_func_node is None,
            )

        type_node = node.child_by_field_name("type")
        if type_node is not None:
            source_fqn = current_func_node.id if current_func_node else class_fqn
            if source_fqn:
                emit_annotation_edges(type_node, code_bytes, source_fqn, file_path, edges)

        if current_func_node:
            if local_types_acc is not None:
                self._functions.collect_assignment_type(
                    node, code_bytes, import_map, current_func_node, local_types_acc
                )
        elif is_module_level_assignment(node, code_bytes, current_func_node):
            # True module level: not in a function (current_func_node) and not
            # in a class body (get_fqn_prefix). Class-body DI aliases are out
            # of scope (2026-06-11-fastapi-di-edges-design.md §6).
            self._functions.process_module_assignment(
                node, code_bytes, file_path, nodes, edges, module_fqn
            )

    def _walk(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        current_func_node: Node | None = None,
        import_map: dict[str, str] | None = None,
        module_fqn: str | None = None,
        local_types_acc: dict[str, dict[str, str]] | None = None,
        self_types_acc: dict[str, dict[str, str]] | None = None,
        star_imports: list[str] | None = None,
        name_refs_acc: list[tuple[str, str, int]] | None = None,
    ) -> None:
        """
        Recursive AST walker that dispatches each node to its handler.
        """
        if node.type in ("import_statement", "import_from_statement"):
            self._imports.handle(
                node, code_bytes, file_path, import_map, module_fqn, edges, star_imports
            )
            return  # never recurse into import nodes

        if node.type == "decorated_definition":
            # star_imports is deliberately not forwarded: `from X import *` is a
            # SyntaxError anywhere but module level, so a decorated definition
            # cannot contain one. Accepting the argument here would imply the
            # path exists (#417 review).
            self._handle_decorated_definition(
                node,
                code_bytes,
                file_path,
                nodes,
                edges,
                import_map,
                module_fqn,
                local_types_acc,
                self_types_acc,
                name_refs_acc,
            )
            return  # prevent double-processing the inner definition

        next_func_node = current_func_node
        if node.type in ("function_definition", "async_function_definition"):
            next_func_node = self._functions.process_function_node(
                node, code_bytes, file_path, nodes, edges, module_fqn or ""
            )
            collect_return_annotation(node, code_bytes, next_func_node, file_path, edges)
        elif node.type == "class_definition":
            self._classes.process_class_node(
                node, code_bytes, file_path, nodes, edges, module_fqn or ""
            )
            next_func_node = None
        elif node.type == "call":
            self._handle_call(node, code_bytes, file_path, edges, current_func_node, module_fqn)
        elif node.type == "assignment":
            self._handle_assignment(
                node,
                code_bytes,
                file_path,
                nodes,
                edges,
                module_fqn or "",
                import_map,
                current_func_node,
                local_types_acc,
                self_types_acc,
            )
        elif (
            node.type in ("typed_parameter", "typed_default_parameter")
            and current_func_node
            and local_types_acc is not None
        ):
            self._functions.collect_param_type(
                node, code_bytes, import_map, current_func_node, local_types_acc, edges
            )
        elif node.type == "identifier" and name_refs_acc is not None:
            self._record_name_load(
                node, code_bytes, file_path, current_func_node, module_fqn, name_refs_acc
            )

        for child in node.children:
            self._walk(
                child,
                code_bytes,
                file_path,
                nodes,
                edges,
                next_func_node,
                import_map=import_map,
                module_fqn=module_fqn,
                local_types_acc=local_types_acc,
                self_types_acc=self_types_acc,
                star_imports=star_imports,
                name_refs_acc=name_refs_acc,
            )

    def _record_name_load(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        current_func_node: Node | None,
        module_fqn: str | None,
        name_refs_acc: list[tuple[str, str, int]],
    ) -> None:
        """Note a name used as a value, for the reference edges emitted after the walk.

        A name that is merely *named* — handed to a framework, caught, put in a
        registry, or used as an enum's head — has no edge of its own, so a class
        used only that way reads as dead (spec D10). Which names count is decided
        after the walk, once the import map and the file's own classes are known.
        """
        if not is_name_load(node):
            return
        owner = self._owner_fqn(node, code_bytes, file_path, current_func_node, module_fqn)
        if owner:
            name_refs_acc.append(
                (
                    owner,
                    code_bytes[node.start_byte : node.end_byte].decode("utf8"),
                    node.start_point.row + 1,
                )
            )

    def _owner_fqn(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        current_func_node: Node | None,
        module_fqn: str | None,
    ) -> str | None:
        """The nearest thing that owns this node: function, else class, else module.

        One rule for every edge whose source is "whatever this code belongs to"
        — calls (#416) and name references (D10) both use it, so they cannot
        drift apart.
        """
        if current_func_node:
            return current_func_node.id
        return (
            enclosing_class_fqn(node, code_bytes, file_path, self._pick_source_root(file_path))
            or module_fqn
        )

    def _handle_call(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        edges: list[Edge],
        current_func_node: Node | None,
        module_fqn: str | None,
    ) -> None:
        """Emit a CALLS edge, attributed to whatever owns the call site.

        A call outside any function used to be dropped entirely, so a class
        built in a module-level registry, a singleton, or DI wiring had no
        incoming edge and read as dead code (#416). The source is the nearest
        thing that owns it: the enclosing function, else the enclosing class,
        else the module.

        `emit_di` is off outside a function because `process_call_node` also
        turns `Depends(x)`/`Security(x)` into DEPENDS_ON edges, and class-body
        DI aliases are out of scope (2026-06-11-fastapi-di-edges-design.md §6).

        Two consequences worth knowing before reading a number off the graph.
        Field defaults now count: `x: int = Field(...)` in a model body is a
        real call from that class, so `_compute_class_coupling` gives every
        such model efferent coupling to `pydantic.Field` and its kin. And a
        call inside a *decorator expression* is still dropped — `_walk` hands
        `decorated_definition` to `_handle_decorated_definition`, which
        descends into the definition and never into the decorators (#429).
        """
        source = self._owner_fqn(node, code_bytes, file_path, current_func_node, module_fqn)
        if source:
            self._functions.process_call_node(
                node,
                code_bytes,
                file_path,
                source,
                edges,
                emit_di=current_func_node is not None,
            )

    def _handle_decorated_definition(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        import_map: dict[str, str] | None,
        module_fqn: str | None,
        local_types_acc: dict[str, dict[str, str]] | None,
        self_types_acc: dict[str, dict[str, str]] | None = None,
        name_refs_acc: list[tuple[str, str, int]] | None = None,
    ) -> None:
        """Process a decorated function or class definition, forwarding decorator names."""
        raw_decorators = extract_decorator_names(node, code_bytes)
        for child in node.children:
            if child.type in ("function_definition", "async_function_definition"):
                inner = self._functions.process_function_node(
                    child,
                    code_bytes,
                    file_path,
                    nodes,
                    edges,
                    module_fqn or "",
                    decorators=raw_decorators,
                )
                collect_return_annotation(child, code_bytes, inner, file_path, edges)
                for grandchild in child.children:
                    self._walk(
                        grandchild,
                        code_bytes,
                        file_path,
                        nodes,
                        edges,
                        inner,
                        import_map=import_map,
                        module_fqn=module_fqn,
                        local_types_acc=local_types_acc,
                        self_types_acc=self_types_acc,
                        name_refs_acc=name_refs_acc,
                    )
            elif child.type == "class_definition":
                self._classes.process_class_node(
                    child,
                    code_bytes,
                    file_path,
                    nodes,
                    edges,
                    module_fqn or "",
                    decorators=raw_decorators,
                )
                for grandchild in child.children:
                    self._walk(
                        grandchild,
                        code_bytes,
                        file_path,
                        nodes,
                        edges,
                        None,
                        import_map=import_map,
                        module_fqn=module_fqn,
                        local_types_acc=local_types_acc,
                        self_types_acc=self_types_acc,
                        name_refs_acc=name_refs_acc,
                    )
