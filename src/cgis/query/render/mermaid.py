"""Implements mermaid queries to render diagram."""

import re

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, Node, NodeNamespace, NodeType
from cgis.extractors.python_extractor import file_path_to_module_fqn

_RAW_CALL_PREFIX = "raw_call:"
_UNRESOLVED_STYLE = ":::unresolvedNode"

# Bare ids that collide with Mermaid keywords break the parser (`end` closes a
# subgraph, etc.); such slugs — and any starting with a digit — are prefixed.
_MERMAID_RESERVED = frozenset(
    {"graph", "subgraph", "end", "class", "classdef", "click", "style", "linkstyle", "direction"}
)
_NON_ID_CHARS = re.compile(r"[^0-9A-Za-z]+")


def _sanitize_token(text: str) -> str:
    """Collapse anything outside [A-Za-z0-9_] to single underscores; never empty."""
    token = _NON_ID_CHARS.sub("_", text).strip("_")
    return token or "node"


def _guard_token(slug: str) -> str:
    """Prefix to dodge a leading digit or a Mermaid reserved word."""
    if slug and (slug[0].isdigit() or slug.lower() in _MERMAID_RESERVED):
        return f"id_{slug}"
    return slug


def _file_stem(file_path: str) -> str:
    """Basename without extension, e.g. ``src/cgis/pipeline.py`` → ``pipeline``."""
    name = file_path.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    return name.rsplit(".", maxsplit=1)[0] or name


def _fqn_slug(fqn: str) -> str:
    """Readable id for a bare FQN (phantom/external/raw_call): its last two segments."""
    body = fqn.removeprefix(_RAW_CALL_PREFIX)
    tail = "_".join(body.split(".")[-2:])
    return _guard_token(_sanitize_token(tail))


def _internal_node_slug(node_id: str, path_fqn: str) -> str | None:
    """Slug an internal node by matching the longest module suffix of its path FQN.

    The extractor builds node ids relative to the ingest root, which may be a
    suffix of the file-path-derived FQN (e.g. id ``cgis.pipeline.run`` when the
    path yields ``src.cgis.pipeline``). Trying successively shorter suffixes
    keeps the ``<file_stem>`` prefix even when the two roots differ. The stem is
    always the deepest segment of the path FQN. Returns None on no match.
    """
    parts = path_fqn.split(".")
    stem = parts[-1]
    for i in range(len(parts)):
        candidate = ".".join(parts[i:])
        if node_id == candidate:
            return _guard_token(_sanitize_token(stem))
        if node_id.startswith(candidate + "."):
            suffix = node_id[len(candidate) + 1 :]
            return _guard_token(_sanitize_token(f"{stem}_{suffix}"))
    return None


def _node_slug(node: Node) -> str:
    """Readable id for a node: ``<file_stem>_<Class>_<method>`` (#210).

    For internal nodes the symbol suffix is peeled off the module FQN derived
    from the file path; module/file nodes slug to the bare stem. Non-internal or
    virtual nodes (and any that don't match the path FQN) fall back to
    :func:`_fqn_slug`. Collisions are disambiguated later by :class:`_IdAllocator`.
    """
    if node.namespace == NodeNamespace.INTERNAL and node.file_path != VIRTUAL_FILE_PATH:
        slug = _internal_node_slug(node.id, file_path_to_module_fqn(node.file_path))
        if slug is not None:
            return slug
    return _fqn_slug(node.id)


class _IdAllocator:
    """Hands out unique, readable, deterministic Mermaid ids for one diagram.

    Same key → same id (idempotent); a fresh slug colliding with an already-used
    one gets a numeric ``_2`` / ``_3`` … suffix, deterministic in call order.
    """

    def __init__(self) -> None:
        """Start with empty key→id and used-id registries."""
        self._by_key: dict[str, str] = {}
        self._used: set[str] = set()

    def _claim(self, key: str, base: str) -> str:
        """Return the id for ``key``, allocating ``base`` (suffixed on collision) if new."""
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        slug = base
        counter = 2
        while slug in self._used:
            slug = f"{base}_{counter}"
            counter += 1
        self._used.add(slug)
        self._by_key[key] = slug
        return slug

    def for_node(self, node: Node) -> str:
        """Allocate (or reuse) the id for a graph node."""
        return self._claim(node.id, _node_slug(node))

    def for_fqn(self, fqn: str) -> str:
        """Allocate (or reuse) the id for a bare FQN endpoint (phantom stub)."""
        return self._claim(fqn, _fqn_slug(fqn))

    def for_subgraph(self, file_path: str) -> str:
        """Allocate (or reuse) the id for a file subgraph block."""
        return self._claim(
            f"sg::{file_path}", _guard_token(_sanitize_token(f"sg_{_file_stem(file_path)}"))
        )


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
        self, file_groups: dict[str, list[Node]], id_map: dict[str, str], alloc: "_IdAllocator"
    ) -> list[str]:
        """Render nodes grouped into subgraph blocks, one block per source file."""
        lines: list[str] = []
        for file_path, group_nodes in file_groups.items():
            sg_id = alloc.for_subgraph(file_path)
            sg_label = _escape(file_path.replace("\\", "/").split("/")[-1])
            lines.append(f'    subgraph {sg_id}["{sg_label}"]')
            lines.extend(self._render_node_line(n, id_map, "        ") for n in group_nodes)
            lines.append("    end")
        return lines

    def _render_edges(
        self, edges: list[Edge], id_map: dict[str, str], alloc: "_IdAllocator"
    ) -> list[str]:
        """Render edge declarations, injecting phantom node stubs for unknown endpoints.

        One arrow per distinct (source, type, target). The graph legitimately holds
        several edges for one such triple — two call sites to the same function, or a
        class named by both a return annotation and a local one — but a diagram that
        draws the same labelled arrow twice states the fact twice and answers no
        question a reader can ask of it. Line numbers, which are what distinguish
        those edges, are not rendered here.
        """
        lines: list[str] = []
        drawn: set[tuple[str, str, str]] = set()
        for edge in edges:
            source_safe = id_map.get(edge.source)
            if not source_safe:
                source_safe = alloc.for_fqn(edge.source)
                lines.append(f'    {source_safe}["{_escape(edge.source)}"]:::defaultNode')
                id_map[edge.source] = source_safe

            target_safe = id_map.get(edge.target)
            if not target_safe:
                target_safe = alloc.for_fqn(edge.target)
                is_unresolved = edge.target.startswith(_RAW_CALL_PREFIX)
                clean_target = _escape(edge.target.removeprefix(_RAW_CALL_PREFIX))
                target_style = _UNRESOLVED_STYLE if is_unresolved else ":::defaultNode"
                lines.append(f'    {target_safe}["{clean_target}"]{target_style}')
                id_map[edge.target] = target_safe

            arrow = (source_safe, edge.type.value, target_safe)
            if arrow in drawn:
                continue
            drawn.add(arrow)
            lines.append(f"    {source_safe} -->|{edge.type.value}| {target_safe}")
        return lines

    def compile(self, nodes: list[Node], edges: list[Edge]) -> str:
        """Generates Mermaid Graph Definition, grouping nodes by file into subgraphs."""
        lines = ["graph TD", *self._style_defs, ""]

        alloc = _IdAllocator()
        id_map = {node.id: alloc.for_node(node) for node in nodes}

        file_groups: dict[str, list[Node]] = {}
        for node in nodes:
            file_groups.setdefault(node.file_path, []).append(node)

        if len(file_groups) > 1:
            lines.extend(self._render_subgraphs(file_groups, id_map, alloc))
        else:
            lines.extend(self._render_node_line(n, id_map, "    ") for n in nodes)

        lines.extend(self._render_edges(edges, id_map, alloc))
        return "\n".join(lines)
