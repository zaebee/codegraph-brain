"""Implements Python Extractor."""

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from tree_sitter import Node as BaseNode

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors.base import BaseExtractor


class PythonExtractor(BaseExtractor):
    """
    A concrete extractor for Python source code using tree-sitter.
    """

    LANG: str = "python"

    def __init__(self) -> None:
        self._language = Language(tspython.language())

    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """
        Extracts structural nodes and edged (Functions, Classes).
        """
        code_bytes = code.encode("utf8")
        parser = Parser()
        parser.language = self._language
        tree = parser.parse(code_bytes)
        root_node: BaseNode = tree.root_node

        nodes: list[Node] = []
        edges: list[Edge] = []

        self._walk(root_node, code_bytes, file_path, nodes, edges)

        return nodes, edges

    def _get_class_prefix(self, node: BaseNode, code_bytes: bytes) -> str | None:
        """Traverse up to find all containing class names, returning them joined by dots."""
        class_parts = []
        curr = node.parent
        while curr:
            if curr.type == "class_definition":
                c_name_node = curr.child_by_field_name("name")
                class_parts.append(self._extract_node_name(c_name_node, code_bytes))
            elif curr.type in ("function_definition", "async_function_definition"):
                break
            curr = curr.parent
        return ".".join(reversed(class_parts)) if class_parts else None

    def _get_id(self, node: BaseNode, code_bytes: bytes, file_path: str) -> str:
        """Generate a fully qualified function/method ID including class context if applicable."""
        name = node.child_by_field_name("name")
        node_name = self._extract_node_name(name, code_bytes)
        prefix = self._get_class_prefix(node, code_bytes)
        full_name = f"{prefix}.{node_name}" if prefix else node_name
        return f"{file_path}:{full_name}"

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
        next_id = current_func_id
        if node.type in ("function_definition", "async_function_definition"):
            self._process_function_node(node, code_bytes, file_path, nodes)
            next_id = self._get_id(node, code_bytes, file_path)
        elif node.type == "class_definition":
            self._process_class_node(node, code_bytes, file_path, nodes)
            next_id = None
        elif node.type == "call" and current_func_id:
            self._process_call_node(node, code_bytes, file_path, current_func_id, edges)

        for child in node.children:
            self._walk(child, code_bytes, file_path, nodes, edges, next_id)

    def _process_function_node(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
    ) -> None:
        """Process function or method definition node."""
        name = node.child_by_field_name("name")
        node_id = self._get_id(node, code_bytes, file_path)
        node_name = self._extract_node_name(name, code_bytes)
        node_type = NodeType.METHOD if self._is_method(node) else NodeType.FUNCTION

        nodes.append(
            Node(
                id=node_id,
                type=node_type,
                name=node_name,
                file_path=file_path,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                language=self.LANG,
            )
        )

    def _process_class_node(
        self, node: BaseNode, code_bytes: bytes, file_path: str, nodes: list[Node]
    ) -> None:
        """Process class definition node."""
        name = node.child_by_field_name("name")
        node_id = self._get_id(node, code_bytes, file_path)
        node_name = self._extract_node_name(name, code_bytes)

        nodes.append(
            Node(
                id=node_id,
                type=NodeType.CLASS,
                name=node_name,
                file_path=file_path,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                language=self.LANG,
            )
        )

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

    def _extract_node_name(self, node: BaseNode | None, code_bytes: bytes) -> str:
        """Extract node name from name node using byte slicing."""
        if node:
            start, end = node.start_byte, node.end_byte
            return code_bytes[start:end].decode("utf8")
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
