"""Implements mermaid queries to render diagram."""

import hashlib

from cgis.core.models import Edge, Node, NodeType

_RAW_CALL_PREFIX = "raw_call:"


def _escape(text: str) -> str:
    """Escape special characters that break Mermaid double-quoted node labels."""
    return (
        text.replace("\\", "\\\\").replace('"', "#quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


class MermaidCompiler:
    """
    Compiles a subgraph (Nodes, Edges) into highly readable, valid Mermaid.js diagrams.
    Handles ID normalization and injects CSS classDefs for clean code visualization.
    """

    def __init__(self) -> None:
        self._style_defs = [
            "classDef classNode fill:#e8f5e9,stroke:#2e7d32,stroke-width:1.5px,color:#1b5e20;",
            "classDef funcNode fill:#e3f2fd,stroke:#1565c0,stroke-width:1.5px,color:#0d47a1;",
            "classDef methodNode fill:#f3e5f5,stroke:#7b1fa2,stroke-width:1.5px,color:#4a148c;",
            (
                "classDef unresolvedNode fill:#fffde7,stroke:#fbc02d,stroke-width:1.5px,"
                "stroke-dasharray: 4 4,color:#f57f17;"
            ),
            "classDef defaultNode fill:#fafafa,stroke:#9e9e9e,stroke-width:1.5px,color:#212121;",
        ]

    def _normalize_id(self, fqn: str) -> str:
        """Deterministically hashes any complex FQN into a Mermaid-safe alphanumeric ID."""
        hasher = hashlib.md5(fqn.encode("utf-8"), usedforsecurity=False)
        return f"n_{hasher.hexdigest()}"

    def _get_node_label(self, node: Node) -> str:
        """Formats the visible text inside a node (Name + file name + line range)."""
        filename = node.file_path.replace("\\", "/").split("/")[-1]
        return _escape(f"{node.name} ({filename}:{node.start_line})")

    def _get_style_class(self, node_type: NodeType, is_unresolved: bool) -> str:
        if is_unresolved:
            return ":::unresolvedNode"
        if node_type == NodeType.CLASS:
            return ":::classNode"
        if node_type == NodeType.FUNCTION:
            return ":::funcNode"
        if node_type == NodeType.METHOD:
            return ":::methodNode"
        return ":::defaultNode"

    def compile(self, nodes: list[Node], edges: list[Edge]) -> str:
        """Generates Mermaid Graph Definition."""
        lines = ["graph TD"]

        lines.extend(self._style_defs)
        lines.append("")  # Empty line for readability

        id_map: dict[str, str] = {}
        for node in nodes:
            safe_id = self._normalize_id(node.id)
            id_map[node.id] = safe_id

            label = self._get_node_label(node)
            is_unresolved = node.id.startswith(_RAW_CALL_PREFIX)
            style = self._get_style_class(node.type, is_unresolved=is_unresolved)

            lines.append(f'    {safe_id}["{label}"]{style}')

        for edge in edges:
            source_safe = id_map.get(edge.source)
            if not source_safe:
                source_safe = self._normalize_id(edge.source)
                lines.append(f'    {source_safe}["{_escape(edge.source)}"]:::defaultNode')
                id_map[edge.source] = source_safe

            target_safe = id_map.get(edge.target)
            if not target_safe:
                target_safe = self._normalize_id(edge.target)
                is_unresolved_target = edge.target.startswith(_RAW_CALL_PREFIX)
                clean_target = _escape(edge.target.removeprefix(_RAW_CALL_PREFIX))
                target_style = ":::unresolvedNode" if is_unresolved_target else ":::defaultNode"
                lines.append(f'    {target_safe}["{clean_target}"]{target_style}')
                id_map[edge.target] = target_safe

            lines.append(f"    {source_safe} -->|{edge.type.value}| {target_safe}")

        return "\n".join(lines)
