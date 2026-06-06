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
        self._parser = Parser()
        self._parser.language = self._language

    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """
        Extracts structural nodes and edged (Functions, Classes).
        """
        code_bytes = code.encode("utf8")
        tree = self._parser.parse(code_bytes)
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
    ) -> None:
        """
        Recursive AST walker.
        """
        # Identify Nodes (Example: Function Definitions)
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            func_name = (
                code_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
                if name_node
                else "unknown"
            )
            func_id = f"{file_path}:{func_name}:{node.start_point[0] + 1}"
            # Determine if this is a method or a regular function
            is_method = False
            curr = node.parent
            while curr:
                if curr.type == "class_definition":
                    is_method = True
                    break
                if curr.type == "function_definition":
                    break
                curr = curr.parent

            node_type = NodeType.METHOD if is_method else NodeType.FUNCTION

            nodes.append(
                Node(
                    id=func_id,
                    type=node_type,
                    name=func_name,
                    file_path=file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    language="python",
                )
            )
            self._find_calls(node, code_bytes, file_path, func_id, edges)

        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            class_name = (
                code_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
                if name_node
                else "unknown"
            )
            class_id = f"{file_path}:{class_name}:{node.start_point[0] + 1}"
            nodes.append(
                Node(
                    id=class_id,
                    type=NodeType.CLASS,
                    name=class_name,
                    file_path=file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    language="python",
                )
            )

        # Recurse
        for child in node.children:
            self._walk(child, code_bytes, file_path, nodes, edges)

    def _get_identifier(self, node: BaseNode, code_bytes: bytes) -> str:
        """Extract name from AST node using byte slicing."""
        if node.type == "identifier":
            return code_bytes[node.start_byte : node.end_byte].decode("utf8")
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
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node:
                return self._get_identifier(func_node, code_bytes)
        for child in node.children:
            if child.type == "identifier":
                return code_bytes[child.start_byte : child.end_byte].decode("utf8")
        return "unknown"

    def _find_calls(
        self, node: BaseNode, code_bytes: bytes, file_path: str, source_id: str, edges: list[Edge]
    ) -> None:
        """
        Finds call expressions and creates 'raw' edges.
        """
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node:
                call_name = self._get_identifier(func_node, code_bytes)
                target_id = f"raw_call:{call_name}"

                edges.append(
                    Edge(
                        id=f"{file_path}:edge_{node.start_byte}",
                        source=source_id,
                        target=target_id,
                        type=EdgeType.CALLS,
                        confidence=0.5,
                        context=f"Call to {call_name}",
                        file_path=file_path,
                        line_number=node.start_point[0] + 1,
                    )
                )

        for child in node.children:
            if child.type not in ("function_definition", "class_definition"):
                self._find_calls(child, code_bytes, file_path, source_id, edges)
