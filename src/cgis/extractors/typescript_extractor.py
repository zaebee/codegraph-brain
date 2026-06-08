"""Implements TypeScript/TSX Extractor using tree-sitter."""

from typing import Any

import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.extractors.base import BaseExtractor

_RAW_CALL_PREFIX = "raw_call:"


def file_path_to_module_fqn(file_path: str) -> str:
    """Convert a TS/TSX file path to a dot-separated module namespace.

    Examples:
        src/api/handler.ts       -> src.api.handler
        src/components/index.tsx -> src.components
        C:\\path\\to\\mod.ts     -> path.to.mod
    """
    clean = file_path
    if len(clean) >= 2 and clean[1] == ":" and clean[0].isalpha():
        clean = clean[2:]
    clean = clean.replace("\\", "/").lstrip("/")
    for ext in (".tsx", ".ts", ".jsx", ".js"):
        if clean.endswith(ext):
            clean = clean[: -len(ext)]
            break
    if clean.endswith("/index"):
        clean = clean[:-6]
    return clean.replace("/", ".")


def _node_text(node: TSNode | None) -> str:
    """Return decoded text of a tree-sitter node, or empty string."""
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _get_name(node: TSNode) -> str:
    """Extract identifier/property_identifier/type_identifier text from a node."""
    for field in ("name",):
        child = node.child_by_field_name(field)
        if child is not None:
            return _node_text(child)
    for child in node.children:
        if child.type in ("identifier", "property_identifier", "type_identifier"):
            return _node_text(child)
    return ""


class TypeScriptExtractor(BaseExtractor):
    """Extracts structural nodes and edges from TypeScript/TSX source files."""

    def __init__(self, tsx: bool = False) -> None:
        """Initialise the tree-sitter TypeScript (or TSX) parser."""
        lang = tsts.language_tsx() if tsx else tsts.language_typescript()
        self._parser = Parser(Language(lang))

    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """Extract nodes and edges from TypeScript source code."""
        code_bytes = code.encode("utf-8")
        tree = self._parser.parse(code_bytes)
        module_fqn = file_path_to_module_fqn(file_path)

        file_node = Node(
            id=file_path,
            name=file_path.replace("\\", "/").split("/")[-1],
            type=NodeType.FILE,
            file_path=file_path,
            start_line=1,
            end_line=tree.root_node.end_point[0] + 1,
            namespace=NodeNamespace.INTERNAL,
        )
        nodes: list[Node] = [file_node]
        edges: list[Edge] = []

        self._walk(
            tree.root_node,
            namespace=module_fqn,
            file_path=file_path,
            file_id=file_path,
            active_class_fqn=None,
            nodes=nodes,
            edges=edges,
        )
        return nodes, edges

    def _walk(
        self,
        node: TSNode,
        namespace: str,
        file_path: str,
        file_id: str,
        active_class_fqn: str | None,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Recursively walk the AST, emitting nodes and edges."""
        inner = node
        # Unwrap export_statement to its declaration child
        if node.type == "export_statement":
            for child in node.children:
                if child.type in (
                    "class_declaration",
                    "function_declaration",
                    "lexical_declaration",
                    "abstract_class_declaration",
                ):
                    inner = child
                    break

        if inner.type in ("class_declaration", "abstract_class_declaration"):
            self._handle_class(inner, namespace, file_path, file_id, nodes, edges)
            return

        if inner.type == "function_declaration":
            self._handle_function(
                inner, namespace, file_path, file_id, active_class_fqn, nodes, edges
            )
            return

        if inner.type == "lexical_declaration":
            self._handle_lexical(
                inner, namespace, file_path, file_id, active_class_fqn, nodes, edges
            )
            return

        if inner.type == "import_statement":
            self._handle_import(inner, namespace, file_path, file_id, nodes, edges)
            return

        for child in node.children:
            self._walk(child, namespace, file_path, file_id, active_class_fqn, nodes, edges)

    def _handle_class(
        self,
        node: TSNode,
        namespace: str,
        file_path: str,
        file_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Emit a CLASS node + CONTAINS edge, then walk its body for methods."""
        name = _get_name(node)
        if not name:
            return
        class_fqn = f"{namespace}.{name}"
        nodes.append(
            Node(
                id=class_fqn,
                name=name,
                type=NodeType.CLASS,
                file_path=file_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                namespace=NodeNamespace.INTERNAL,
            )
        )
        edges.append(
            Edge(
                id=f"{file_id}->{class_fqn}",
                source=file_id,
                target=class_fqn,
                type=EdgeType.CONTAINS,
            )
        )
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                if child.type == "method_definition":
                    self._handle_method(child, class_fqn, file_path, nodes, edges)

    def _handle_method(
        self,
        node: TSNode,
        class_fqn: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Emit a METHOD node + DECLARES edge, then find calls in the body."""
        name = _get_name(node)
        if not name or name in ("#", ""):
            return
        method_fqn = f"{class_fqn}.{name}"
        nodes.append(
            Node(
                id=method_fqn,
                name=name,
                type=NodeType.METHOD,
                file_path=file_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                namespace=NodeNamespace.INTERNAL,
            )
        )
        edges.append(
            Edge(
                id=f"{class_fqn}->{method_fqn}",
                source=class_fqn,
                target=method_fqn,
                type=EdgeType.DECLARES,
            )
        )
        self._find_calls(node, method_fqn, edges)

    def _handle_function(
        self,
        node: TSNode,
        namespace: str,
        file_path: str,
        file_id: str,
        active_class_fqn: str | None,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Emit a FUNCTION node + CONTAINS edge, then find calls in the body."""
        name = _get_name(node)
        if not name:
            return
        parent_id = active_class_fqn or file_id
        func_fqn = f"{namespace}.{name}"
        nodes.append(
            Node(
                id=func_fqn,
                name=name,
                type=NodeType.FUNCTION,
                file_path=file_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                namespace=NodeNamespace.INTERNAL,
            )
        )
        edges.append(
            Edge(
                id=f"{parent_id}->{func_fqn}",
                source=parent_id,
                target=func_fqn,
                type=EdgeType.CONTAINS,
            )
        )
        self._find_calls(node, func_fqn, edges)

    def _handle_lexical(
        self,
        node: TSNode,
        namespace: str,
        file_path: str,
        file_id: str,
        active_class_fqn: str | None,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Handle const/let arrow-function declarations."""
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            value = child.child_by_field_name("value")
            if value is None or value.type != "arrow_function":
                continue
            name_node = child.child_by_field_name("name")
            name = _node_text(name_node)
            if not name:
                continue
            parent_id = active_class_fqn or file_id
            func_fqn = f"{namespace}.{name}"
            nodes.append(
                Node(
                    id=func_fqn,
                    name=name,
                    type=NodeType.FUNCTION,
                    file_path=file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    namespace=NodeNamespace.INTERNAL,
                )
            )
            edges.append(
                Edge(
                    id=f"{parent_id}->{func_fqn}",
                    source=parent_id,
                    target=func_fqn,
                    type=EdgeType.CONTAINS,
                )
            )
            self._find_calls(value, func_fqn, edges)

    def _handle_import(
        self,
        node: TSNode,
        namespace: str,
        file_path: str,  # noqa: ARG002
        file_id: str,
        nodes: list[Node],  # noqa: ARG002
        edges: list[Edge],
    ) -> None:
        """Emit IMPORTS edges for each import statement."""
        source_node = node.child_by_field_name("source")
        raw_source = _node_text(source_node).strip("'\"")
        if not raw_source:
            return
        # Resolve relative imports to FQN
        if raw_source.startswith("."):
            parts = namespace.split(".")
            clean = raw_source.lstrip("./").replace("/", ".")
            # one dot → same dir, two dots → parent dir
            dots = len(raw_source) - len(raw_source.lstrip("."))
            base_parts = parts[: max(0, len(parts) - (dots - 1))]
            target_fqn = ".".join(base_parts + ([clean] if clean else []))
        else:
            target_fqn = raw_source.replace("/", ".")

        edges.append(
            Edge(
                id=f"{file_id}->import:{target_fqn}",
                source=file_id,
                target=target_fqn,
                type=EdgeType.IMPORTS,
            )
        )

    def _find_calls(
        self,
        node: TSNode,
        source_id: str,
        edges: list[Edge],
    ) -> None:
        """Recursively find call_expression nodes and emit CALLS edges."""
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                call_name = self._get_call_name(func_node)
                if call_name:
                    target = f"{_RAW_CALL_PREFIX}{call_name}"
                    edge_id = f"{source_id}->{target}@{node.start_point[0]}"
                    edges.append(
                        Edge(
                            id=edge_id,
                            source=source_id,
                            target=target,
                            type=EdgeType.CALLS,
                            confidence=0.1,
                        )
                    )
        for child in node.children:
            self._find_calls(child, source_id, edges)

    def _get_call_name(self, node: TSNode) -> str:
        """Extract a call target name from identifier or member_expression."""
        if node.type == "identifier":
            return _node_text(node)
        if node.type == "member_expression":
            obj = node.child_by_field_name("object")
            prop = node.child_by_field_name("property")
            obj_text = _node_text(obj)
            prop_text = _node_text(prop)
            if obj_text and prop_text:
                # Normalise 'this' → mirrors Python's 'self' for resolver
                return f"self.{prop_text}" if obj_text == "this" else f"{obj_text}.{prop_text}"
        return ""

    # Required by BaseExtractor — tree-sitter node access is via public API
    def _get_ts_node_field(self, node: Any, field: str) -> Any:  # noqa: ANN401
        """Delegate to tree-sitter child_by_field_name (unused externally)."""
        return node.child_by_field_name(field)
