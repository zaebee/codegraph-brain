"""Implements Python Extractor."""

from tree_sitter import Node as BaseNode
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors.base import BaseExtractor


class PythonExtractor(BaseExtractor):
    """
    A concrete extractor for Python source code using tree-sitter.
    """

    def __init__(self) -> None:
        self._language = get_language("python")

    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """
        Extracts structural nodes and edged (Functions, Classes).
        """
        parser = Parser(self._language)
        code_bytes = code.encode("utf8")
        tree = parser.parse(code_bytes)
        root_node: BaseNode = tree.root_node

        nodes: list[Node] = []
        edges: list[Edge] = []

        self._walk(root_node, code_bytes, file_path, nodes, edges)

        return nodes, edges

    def _walk(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        current_func_id: str | None = None,
    ) -> None:
        """
        Recursive AST walker that extracts nodes and edges in a single pass.
        """
        next_func_id = current_func_id
        if node.type in ("function_definition", "async_function_definition"):
            self._process_function_node(node, code_bytes, file_path, nodes, edges)
            name_node = node.child_by_field_name("name")
            func_name = self._extract_node_name(name_node, code_bytes)
            next_func_id = f"{file_path}:{func_name}:{node.start_point.row + 1}"
        elif node.type == "class_definition":
            self._process_class_node(node, code_bytes, file_path, nodes)
            next_func_id = None
        elif node.type == "call" and current_func_id:
            self._process_call_node(node, code_bytes, file_path, current_func_id, edges)

        for child in node.children:
            self._walk(child, code_bytes, file_path, nodes, edges, next_func_id)

    def _process_function_node(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        _edges: list[Edge],
    ) -> None:
        """Process function or method definition node."""
        name_node = node.child_by_field_name("name")
        func_name = self._extract_node_name(name_node, code_bytes)
        func_id = f"{file_path}:{func_name}:{node.start_point.row + 1}"
        node_type = NodeType.METHOD if self._is_method(node) else NodeType.FUNCTION

        nodes.append(
            Node(
                id=func_id,
                type=node_type,
                name=func_name,
                file_path=file_path,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                language="python",
            )
        )

    def _process_class_node(
        self, node: BaseNode, code_bytes: bytes, file_path: str, nodes: list[Node]
    ) -> None:
        """Process class definition node."""
        name_node = node.child_by_field_name("name")
        class_name = self._extract_node_name(name_node, code_bytes)
        class_id = f"{file_path}:{class_name}:{node.start_point.row + 1}"

        nodes.append(
            Node(
                id=class_id,
                type=NodeType.CLASS,
                name=class_name,
                file_path=file_path,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                language="python",
            )
        )

    def _is_method(self, node: BaseNode) -> bool:
        """Check if a function node is a method (defined inside a class)."""
        curr = node.parent
        while curr:
            if curr.type == "class_definition":
                return True
            if curr.type in ("function_definition", "async_function_definition"):
                return False
            curr = curr.parent
        return False

    def _extract_node_name(self, name_node: BaseNode | None, code_bytes: bytes) -> str:
        """Extract node name from name node using byte slicing."""
        if name_node:
            return code_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
        return "unknown"

    def _get_identifier(self, node: BaseNode, code_bytes: bytes) -> str:
        """Extract name from AST node using byte slicing."""
        if node.type == "identifier":
            return code_bytes[node.start_byte : node.end_byte].decode("utf8")
        if node.type == "parenthesized_expression":
            for child in node.children:
                if child.type not in ("(", ")"):
                    return self._get_identifier(child, code_bytes)
        if node.type in ("attribute", "call", "subscript"):
            return self._extract_nested_name(node, code_bytes)
        for child in node.children:
            if child.type == "identifier":
                return code_bytes[child.start_byte : child.end_byte].decode("utf8")
        return "unknown"

    def _extract_nested_name(self, node: BaseNode, code_bytes: bytes) -> str:
        """Extract nested identifier from attribute/call/subscript nodes."""
        if node.type == "attribute":
            obj_node = node.child_by_field_name("object")
            attr_node = node.child_by_field_name("attribute")
            if obj_node and attr_node:
                return (
                    f"{self._get_identifier(obj_node, code_bytes)}"
                    f".{self._get_identifier(attr_node, code_bytes)}"
                )
            if attr_node:
                return self._get_identifier(attr_node, code_bytes)
        elif node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node:
                return self._get_identifier(func_node, code_bytes)
        elif node.type == "subscript":
            value_node = node.child_by_field_name("value")
            if value_node:
                return self._get_identifier(value_node, code_bytes)
        return "unknown"

    def _process_call_node(
        self, node: BaseNode, code_bytes: bytes, file_path: str, source_id: str, edges: list[Edge]
    ) -> None:
        """
        Finds call expressions node.
        """
        func_node = node.child_by_field_name("function")
        if func_node:
            call_name = self._get_identifier(func_node, code_bytes)
            if call_name == "unknown":
                return
            target_id = f"raw_call:{call_name}"

            edges.append(
                Edge(
                    id=f"{file_path}:edge_{node.start_byte}_{node.end_byte}",
                    source=source_id,
                    target=target_id,
                    type=EdgeType.CALLS,
                    confidence=0.5,
                    context=f"Call to {call_name}",
                    file_path=file_path,
                    line_number=node.start_point.row + 1,
                )
            )
