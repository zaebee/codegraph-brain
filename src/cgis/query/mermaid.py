"""Implements mermaid queries to render diagram."""

import hashlib

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, Node, NodeNamespace, NodeType

_RAW_CALL_PREFIX = "raw_call:"
_UNRESOLVED_STYLE = ":::unresolvedNode"


def _normalize_id(fqn: str) -> str:
    """Deterministically hash any complex FQN into a Mermaid-safe alphanumeric ID."""
    hasher = hashlib.md5(fqn.encode("utf-8"), usedforsecurity=False)
    return f"n_{hasher.hexdigest()}"


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
        """Initialise with the default CSS class definitions for node styling."""
        self._style_defs = [
            "classDef classNode fill:#e8f5e9,stroke:#2e7d32,stroke-width:1.5px,color:#1b5e20;",
            "classDef funcNode fill:#e3f2fd,stroke:#1565c0,stroke-width:1.5px,color:#0d47a1;",
            "classDef methodNode fill:#f3e5f5,stroke:#7b1fa2,stroke-width:1.5px,color:#4a148c;",
            (
                "classDef unresolvedNode fill:#fffde7,stroke:#fbc02d,stroke-width:1.5px,"
                "stroke-dasharray: 4 4,color:#f57f17;"
            ),
            "classDef defaultNode fill:#fafafa,stroke:#9e9e9e,stroke-width:1.5px,color:#212121;",
            "classDef stdlibNode fill:#eceff1,stroke:#607d8b,stroke-width:1px,color:#455a64;",
            (
                "classDef externalNode fill:#fff3e0,stroke:#e65100,stroke-width:1px,"
                "stroke-dasharray: 3 3,color:#bf360c;"
            ),
        ]

    def _get_node_label(self, node: Node) -> str:
        """Formats the visible text inside a node (Name + file name + line range)."""
        filename = node.file_path.replace("\\", "/").split("/")[-1]
        return _escape(f"{node.name} ({filename}:{node.start_line})")

    def _get_style_class(self, node: Node) -> str:
        """Map a node to its Mermaid CSS class suffix based on namespace and type."""
        if node.namespace == NodeNamespace.STDLIB:
            return ":::stdlibNode"
        if node.namespace == NodeNamespace.EXTERNAL:
            return ":::externalNode"
        if node.namespace == NodeNamespace.UNKNOWN:
            return _UNRESOLVED_STYLE
        if node.file_path == VIRTUAL_FILE_PATH and node.namespace == NodeNamespace.INTERNAL:
            return _UNRESOLVED_STYLE
        if node.type == NodeType.CLASS:
            return ":::classNode"
        if node.type == NodeType.FUNCTION:
            return ":::funcNode"
        if node.type == NodeType.METHOD:
            return ":::methodNode"
        return ":::defaultNode"

    def _render_node_line(self, node: Node, id_map: dict[str, str], indent: str) -> str:
        """Format a single node declaration with its label and style class."""
        safe_id = id_map[node.id]
        return f'{indent}{safe_id}["{self._get_node_label(node)}"]{self._get_style_class(node)}'

    def _render_subgraphs(
        self, file_groups: dict[str, list[Node]], id_map: dict[str, str]
    ) -> list[str]:
        """Render nodes grouped into subgraph blocks, one block per source file."""
        lines: list[str] = []
        for file_path, group_nodes in file_groups.items():
            sg_id = _normalize_id(f"sg_{file_path}")
            sg_label = _escape(file_path.replace("\\", "/").split("/")[-1])
            lines.append(f'    subgraph {sg_id}["{sg_label}"]')
            lines.extend(self._render_node_line(n, id_map, "        ") for n in group_nodes)
            lines.append("    end")
        return lines

    def _render_edges(self, edges: list[Edge], id_map: dict[str, str]) -> list[str]:
        """Render edge declarations, injecting phantom node stubs for unknown endpoints."""
        lines: list[str] = []
        for edge in edges:
            source_safe = id_map.get(edge.source)
            if not source_safe:
                source_safe = _normalize_id(edge.source)
                lines.append(f'    {source_safe}["{_escape(edge.source)}"]:::defaultNode')
                id_map[edge.source] = source_safe

            target_safe = id_map.get(edge.target)
            if not target_safe:
                target_safe = _normalize_id(edge.target)
                is_unresolved = edge.target.startswith(_RAW_CALL_PREFIX)
                clean_target = _escape(edge.target.removeprefix(_RAW_CALL_PREFIX))
                target_style = _UNRESOLVED_STYLE if is_unresolved else ":::defaultNode"
                lines.append(f'    {target_safe}["{clean_target}"]{target_style}')
                id_map[edge.target] = target_safe

            lines.append(f"    {source_safe} -->|{edge.type.value}| {target_safe}")
        return lines

    def compile(self, nodes: list[Node], edges: list[Edge]) -> str:
        """Generates Mermaid Graph Definition, grouping nodes by file into subgraphs."""
        lines = ["graph TD", *self._style_defs, ""]

        id_map = {node.id: _normalize_id(node.id) for node in nodes}

        file_groups: dict[str, list[Node]] = {}
        for node in nodes:
            file_groups.setdefault(node.file_path, []).append(node)

        if len(file_groups) > 1:
            lines.extend(self._render_subgraphs(file_groups, id_map))
        else:
            lines.extend(self._render_node_line(n, id_map, "    ") for n in nodes)

        lines.extend(self._render_edges(edges, id_map))
        return "\n".join(lines)
