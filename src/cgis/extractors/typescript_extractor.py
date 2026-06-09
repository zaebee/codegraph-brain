"""Implements TypeScript/TSX Extractor using tree-sitter."""

import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.extractors.base import BaseExtractor

_RAW_CALL_PREFIX = "raw_call:"
_EXPORT_UNWRAP_TYPES = frozenset(
    {
        "class_declaration",
        "abstract_class_declaration",
        "function_declaration",
        "lexical_declaration",
    }
)


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
    child = node.child_by_field_name("name")
    if child is not None:
        return _node_text(child)
    for child in node.children:
        if child.type in ("identifier", "property_identifier", "type_identifier"):
            return _node_text(child)
    return ""


def _make_node(fqn: str, name: str, node_type: NodeType, file_path: str, ts_node: TSNode) -> Node:
    """Construct an internal Node from a tree-sitter node's position."""
    return Node(
        id=fqn,
        name=name,
        type=node_type,
        file_path=file_path,
        start_line=ts_node.start_point[0] + 1,
        end_line=ts_node.end_point[0] + 1,
        namespace=NodeNamespace.INTERNAL,
    )


def _resolve_relative_import(namespace: str, raw_source: str) -> str:
    """Resolve a relative import path to a dot-separated module FQN.

    Walks segments so that ``./utils``, ``../api``, and ``../../lib`` all
    produce the correct absolute FQN relative to *namespace*.
    """
    dir_parts = namespace.split(".")[:-1]
    for segment in raw_source.replace("\\", "/").split("/"):
        if segment == ".." and dir_parts:
            dir_parts.pop()
        elif segment and segment not in (".", ".."):
            dir_parts.append(segment)
    if dir_parts and dir_parts[-1] == "index":
        dir_parts.pop()
    return ".".join(dir_parts)


def _make_edge(source: str, target: str, edge_type: EdgeType) -> Edge:
    """Construct an Edge with a deterministic id."""
    return Edge(id=f"{source}->{target}", source=source, target=target, type=edge_type)


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
        if node.type == "export_statement":
            for child in node.children:
                if child.type in _EXPORT_UNWRAP_TYPES:
                    inner = child
                    break

        if inner.type in ("class_declaration", "abstract_class_declaration"):
            self._handle_class(inner, namespace, file_path, file_id, nodes, edges)
        elif inner.type == "function_declaration":
            self._handle_function(
                inner, namespace, file_path, file_id, active_class_fqn, nodes, edges
            )
        elif inner.type == "lexical_declaration":
            self._handle_lexical(
                inner, namespace, file_path, file_id, active_class_fqn, nodes, edges
            )
        elif inner.type == "import_statement":
            self._handle_import(inner, namespace, file_id, edges)
        else:
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
        nodes.append(_make_node(class_fqn, name, NodeType.CLASS, file_path, node))
        edges.append(_make_edge(file_id, class_fqn, EdgeType.CONTAINS))
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                if child.type in ("method_definition", "abstract_method_signature"):
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
        if not name:
            return
        method_fqn = f"{class_fqn}.{name}"
        nodes.append(_make_node(method_fqn, name, NodeType.METHOD, file_path, node))
        edges.append(_make_edge(class_fqn, method_fqn, EdgeType.DECLARES))
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
        func_fqn = f"{namespace}.{name}"
        nodes.append(_make_node(func_fqn, name, NodeType.FUNCTION, file_path, node))
        edges.append(_make_edge(active_class_fqn or file_id, func_fqn, EdgeType.CONTAINS))
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
            name = _node_text(child.child_by_field_name("name"))
            if not name:
                continue
            func_fqn = f"{namespace}.{name}"
            nodes.append(_make_node(func_fqn, name, NodeType.FUNCTION, file_path, node))
            edges.append(_make_edge(active_class_fqn or file_id, func_fqn, EdgeType.CONTAINS))
            self._find_calls(value, func_fqn, edges)

    def _handle_import(
        self,
        node: TSNode,
        namespace: str,
        file_id: str,
        edges: list[Edge],
    ) -> None:
        """Emit an IMPORTS edge for an import statement."""
        raw_source = _node_text(node.child_by_field_name("source")).strip("'\"")
        if not raw_source:
            return
        if raw_source.startswith("."):
            target_fqn = _resolve_relative_import(namespace, raw_source)
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

    def _find_calls(self, node: TSNode, source_id: str, edges: list[Edge]) -> None:
        """Recursively find call_expression nodes and emit CALLS edges."""
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                call_name = self._get_call_name(func_node)
                if call_name:
                    target = f"{_RAW_CALL_PREFIX}{call_name}"
                    edges.append(
                        Edge(
                            id=f"{source_id}->{target}@{node.start_point[0]}:{node.start_point[1]}",
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
            obj_text = _node_text(node.child_by_field_name("object"))
            prop_text = _node_text(node.child_by_field_name("property"))
            if obj_text and prop_text:
                return f"self.{prop_text}" if obj_text == "this" else f"{obj_text}.{prop_text}"
        return ""
