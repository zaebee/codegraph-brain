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
        parser = Parser()
        parser.language = self._language

        code_bytes = code.encode("utf8")
        tree = parser.parse(code_bytes)
        root_node: BaseNode = tree.root_node

        nodes: list[Node] = []
        edges: list[Edge] = []

        self._walk(root_node, code_bytes, file_path, nodes, edges)

        return nodes, edges

    def _get_fqn_prefix(self, node: BaseNode, code_bytes: bytes) -> str | None:
        """Traverse up to find class and function names, returning them joined by dots."""
        parts = []
        curr = node.parent
        extract_types = ("class_definition", "function_definition", "async_function_definition")
        while curr:
            if curr.type in extract_types:
                name_node = curr.child_by_field_name("name")
                parts.append(self._extract_node_name(name_node, code_bytes))
            curr = curr.parent
        return ".".join(reversed(parts)) if parts else None

    def _get_id(self, node: BaseNode, code_bytes: bytes, file_path: str) -> str:
        """Generate a fully qualified function/method ID including class/function context."""
        name = node.child_by_field_name("name")
        node_name = self._extract_node_name(name, code_bytes)
        prefix = self._get_fqn_prefix(node, code_bytes)
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
            next_id = self._process_function_node(node, code_bytes, file_path, nodes)
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
    ) -> str:
        """Process function or method definition node."""
        child = node.child_by_field_name("name")
        node_id = self._get_id(node, code_bytes, file_path)
        node_name = self._extract_node_name(child, code_bytes)
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
        return node_id

    def _process_class_node(
        self, node: BaseNode, code_bytes: bytes, file_path: str, nodes: list[Node]
    ) -> None:
        """Process class definition node."""
        child = node.child_by_field_name("name")
        node_id = self._get_id(node, code_bytes, file_path)
        node_name = self._extract_node_name(child, code_bytes)
        node_type = NodeType.CLASS

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

    def _process_call_node(
        self, node: BaseNode, code_bytes: bytes, file_path: str, source_id: str, edges: list[Edge]
    ) -> None:
        """
        Finds call expressions node.
        """
        child = node.child_by_field_name("function")
        edge_id = f"{file_path}:edge_{node.start_byte}_{node.end_byte}"
        if child:
            call_name = self._get_identifier(child, code_bytes)
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
            return code_bytes[start:end].decode("utf8", errors="replace")
        return "unknown"

    def _get_identifier(self, node: BaseNode, code_bytes: bytes) -> str:
        """Extract name from AST node using byte slicing."""
        if node.type == "identifier":
            start, end = node.start_byte, node.end_byte
            return code_bytes[start:end].decode("utf8", errors="replace")
        if node.type == "parenthesized_expression":
            for child in node.children:
                if child.type not in ("(", ")", "comment"):
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
                obj_id = self._get_identifier(obj_node, code_bytes)
                attr_id = self._get_identifier(attr_node, code_bytes)
                return (
                    f"{obj_id}.{attr_id}"
                    if obj_id != "unknown" and attr_id != "unknown"
                    else "unknown"
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
