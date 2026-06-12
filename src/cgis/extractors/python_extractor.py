"""Implements Python Extractor."""

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from tree_sitter import Node as BaseNode

from cgis.core.models import Edge, Node, NodeType
from cgis.extractors._python_ast import extract_decorator_names, is_module_level_assignment
from cgis.extractors._python_ast import file_path_to_module_fqn as _file_path_to_module_fqn
from cgis.extractors._python_classes import ClassHandler
from cgis.extractors._python_functions import FunctionHandler
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

        self._walk(
            root_node,
            code_bytes,
            file_path,
            nodes,
            edges,
            import_map=import_map,
            module_fqn=module_fqn,
            local_types_acc=local_types_acc,
        )

        # Apply accumulated local types from assignments and param annotations
        nodes_by_id = {n.id: i for i, n in enumerate(nodes)}
        for func_id, lt in local_types_acc.items():
            if func_id in nodes_by_id:
                i = nodes_by_id[func_id]
                nodes[i] = nodes[i].model_copy(
                    update={"metadata": {**nodes[i].metadata, "local_types": lt}}
                )

        file_node = Node(
            id=module_fqn,
            type=NodeType.FILE,
            name=file_path.rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1],
            file_path=file_path,
            start_line=1,
            end_line=root_node.end_point.row + 1,
            metadata={"import_map": import_map},
        )
        nodes.insert(0, file_node)

        return nodes, edges

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
    ) -> None:
        """
        Recursive AST walker that dispatches each node to its handler.
        """
        if node.type in ("import_statement", "import_from_statement"):
            self._imports.handle(node, code_bytes, file_path, import_map, module_fqn, edges)
            return  # never recurse into import nodes

        if node.type == "decorated_definition":
            self._handle_decorated_definition(
                node,
                code_bytes,
                file_path,
                nodes,
                edges,
                import_map,
                module_fqn,
                local_types_acc,
            )
            return  # prevent double-processing the inner definition

        next_func_node = current_func_node
        if node.type in ("function_definition", "async_function_definition"):
            next_func_node = self._functions.process_function_node(
                node, code_bytes, file_path, nodes, edges, module_fqn or ""
            )
        elif node.type == "class_definition":
            self._classes.process_class_node(
                node, code_bytes, file_path, nodes, edges, module_fqn or ""
            )
            next_func_node = None
        elif node.type == "call" and current_func_node:
            self._functions.process_call_node(
                node, code_bytes, file_path, current_func_node.id, edges
            )
        elif node.type == "assignment" and current_func_node and local_types_acc is not None:
            self._functions.collect_assignment_type(
                node, code_bytes, import_map, current_func_node, local_types_acc
            )
        elif is_module_level_assignment(node, code_bytes, current_func_node):
            # True module level: not in a function (current_func_node) and not
            # in a class body (get_fqn_prefix). Class-body DI aliases are out
            # of scope (spec §6).
            self._functions.process_module_assignment(
                node, code_bytes, file_path, nodes, edges, module_fqn or ""
            )
        elif (
            node.type in ("typed_parameter", "typed_default_parameter")
            and current_func_node
            and local_types_acc is not None
        ):
            self._functions.collect_param_type(
                node, code_bytes, import_map, current_func_node, local_types_acc, edges
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
                    )
