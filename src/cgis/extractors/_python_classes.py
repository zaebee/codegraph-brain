"""Class-definition handling for the Python extractor.

Emits CLASS nodes plus their CONTAINS/EXTENDS structural edges and the
abstract-class / metaclass metadata.
"""

from collections.abc import Callable
from typing import Any

from tree_sitter import Node as BaseNode

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors._python_ast import (
    PYTHON_LANG,
    extract_node_name,
    get_id,
    get_identifier,
)

_ABC_NAMES: frozenset[str] = frozenset({"ABC", "ABCMeta"})


class ClassHandler:
    """Extracts CLASS nodes and their inheritance metadata from class AST nodes."""

    def __init__(self, pick_source_root: Callable[[str], str | None]) -> None:
        """Store the per-file source-root picker used for FQN construction."""
        self._pick_source_root = pick_source_root

    def process_class_node(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        module_fqn: str,
        decorators: list[str] | None = None,
    ) -> None:
        """Process class definition node."""
        child = node.child_by_field_name("name")
        node_id = get_id(node, code_bytes, file_path, self._pick_source_root(file_path))
        node_name = extract_node_name(child, code_bytes)

        superclass_names = self._collect_superclasses(node, node_id, file_path, code_bytes, edges)

        metadata: dict[str, Any] = {}
        if decorators:
            metadata["decorators"] = decorators
        if self._is_abstract_class(superclass_names, decorators):
            metadata["is_abstract"] = True

        nodes.append(
            Node(
                id=node_id,
                type=NodeType.CLASS,
                name=node_name,
                file_path=file_path,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                language=PYTHON_LANG,
                metadata=metadata,
            )
        )

        parts = node_id.rsplit(".", maxsplit=1)
        parent_fqn = parts[0] if len(parts) > 1 else module_fqn
        edges.append(
            Edge(
                id=f"{parent_fqn}:structural:{node_id}",
                type=EdgeType.CONTAINS,
                source=parent_fqn,
                target=node_id,
                confidence=1.0,
                file_path=file_path,
            )
        )

    def _collect_superclasses(
        self,
        node: BaseNode,
        node_id: str,
        file_path: str,
        code_bytes: bytes,
        edges: list[Edge],
    ) -> list[str]:
        """Collect superclass names and emit EXTENDS edges for a class definition node."""
        names: list[str] = []
        superclasses_node = node.child_by_field_name("superclasses")
        if not superclasses_node:
            return names
        for sc in superclasses_node.children:
            if sc.type in ("identifier", "attribute", "subscript"):
                sc_name = get_identifier(sc, code_bytes)
                if sc_name != "unknown":
                    names.append(sc_name)
                    edges.append(
                        Edge(
                            id=f"{node_id}:extends:{sc_name}",
                            type=EdgeType.EXTENDS,
                            source=node_id,
                            target=f"raw_class:{sc_name}",
                            confidence=1.0,
                            file_path=file_path,
                        )
                    )
            elif sc.type == "keyword_argument":
                meta_name = self._extract_metaclass_name(sc, code_bytes)
                if meta_name:
                    names.append(meta_name)
        return names

    @staticmethod
    def _is_abstract_class(superclass_names: list[str], decorators: list[str] | None) -> bool:
        """Return True if class should be marked abstract (ABC/ABCMeta in bases or decorators)."""
        candidates = (*superclass_names, *(decorators or []))
        return any(n in _ABC_NAMES or n.rpartition(".")[-1] in _ABC_NAMES for n in candidates)

    def _extract_metaclass_name(self, node: BaseNode, code_bytes: bytes) -> str | None:
        """Return the metaclass name from a keyword_argument node like `metaclass=ABCMeta`."""
        name_node = node.child_by_field_name("name")
        value_node = node.child_by_field_name("value")
        if name_node and value_node and get_identifier(name_node, code_bytes) == "metaclass":
            meta_name = get_identifier(value_node, code_bytes)
            return meta_name if meta_name != "unknown" else None
        return None
