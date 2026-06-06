"""Implements Python Extractor."""

from tree_sitter import Parser
from tree_sitter.binding import Node as BaseNode
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
        self._parser.set_language(self._language)

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
        # 1. Identify Nodes (Example: Function Definitions)
        if node.type == "function_definition":
            func_name = self._get_identifier(node, code_bytes)
            func_id = f"{file_path}:{func_name}:{node.start_point[0]}"

            nodes.append(
                Node(
                    id=func_id,
                    type=NodeType.FUNCTION,
                    name=func_name,
                    file_path=file_path,
                    start_line=node.start_point[0],
                    end_line=node.end_point[0],
                    language="python",
                )
            )

            self._find_calls(node, code_bytes, file_path, func_id, edges)

        # 3. Recurse
        for child in node.children:
            self._walk(child, code_bytes, file_path, nodes, edges)

    def _get_identifier(self, node: BaseNode, code_bytes: bytes) -> str:
        """Extract name from AST node using byte slicing."""
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
                        id=f"edge_{node.start_byte}",
                        source=source_id,
                        target=target_id,
                        type=EdgeType.CALLS,
                        confidence=0.5,
                        context=f"Call to {call_name}",
                    )
                )

        for child in node.children:
            self._find_calls(child, code_bytes, file_path, source_id, edges)
