"""Semantic Uplift Engine — enriches the code graph with ontology classes and domain tags."""

from __future__ import annotations

import fnmatch
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from cgis.core.models import (
    VIRTUAL_FILE_PATH,
    Edge,
    EdgeType,
    Node,
    NodeNamespace,
    NodeType,
)

if TYPE_CHECKING:
    from cgis.storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Ontology class mapping table (Phase 1)
# ---------------------------------------------------------------------------

_ONTOLOGY_CLASS: dict[NodeType, str] = {
    NodeType.FILE: "File",
    NodeType.MODULE: "Module",
    NodeType.CLASS: "Class",
    NodeType.FUNCTION: "Function",
    NodeType.METHOD: "Method",
    NodeType.VARIABLE: "Variable",
    NodeType.IMPORT: "Import",
    NodeType.EXPORT: "Export",
    NodeType.API_ENDPOINT: "APIEndpoint",
    NodeType.ROUTE_HANDLER: "RouteHandler",
    NodeType.DB_TABLE: "DBTable",
    NodeType.DB_QUERY: "DBQuery",
    NodeType.EVENT: "Event",
    NodeType.DOMAIN_CONCEPT: "DomainConcept",
}

_STRUCTURAL_EDGE_TYPES = frozenset({EdgeType.CONTAINS, EdgeType.DECLARES})


def _load_domains_config(config_path: str) -> dict[str, Any]:
    with Path(config_path).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        return {}
    domains: dict[str, Any] = data.get("domains") or {}
    return domains


class SemanticUpliftEngine:
    """
    Post-processing pass that enriches the graph with semantic metadata.

    Run after ResolverEngine has produced fully resolved edges. Operates
    directly on the SQLiteStore — reads, updates, and saves in-place.

    Phases:
      1. Ontology class mapping  — NodeType → ontology_class string
      2. Heuristic domain tagging — file_path / fqn pattern matching from domains.yaml
      3. Structural propagation  — domains flow FILE → CLASS → METHOD via CONTAINS/DECLARES
      4. Domain dependency inference — emit DOMAIN_DEPENDS_ON edges from cross-domain CALLS
    """

    def __init__(
        self,
        store: SQLiteStore,
        domains_config_path: str | None = None,
    ) -> None:
        self._store = store
        self._domains: dict[str, Any] = {}
        if domains_config_path:
            self._domains = _load_domains_config(domains_config_path)

    def execute_uplift(self) -> None:
        """Run all phases against the store in a single pass."""
        self._store.delete_semantic_uplift()
        nodes = self._store.get_all_nodes()
        edges = self._store.get_all_edges()

        original_nodes: dict[str, Node] = {n.id: n for n in nodes}
        nodes_map = dict(original_nodes)

        nodes_map = _phase1_map_ontology_classes(nodes_map)

        if self._domains:
            nodes_map = _phase2_apply_heuristic_tagging(nodes_map, self._domains)
            nodes_map = _phase3_propagate_domains(nodes_map, edges)

        domain_nodes, domain_edges = _phase4_infer_domain_dependencies(nodes_map, edges)

        changed_nodes = [n for node_id, n in nodes_map.items() if n is not original_nodes[node_id]]
        if changed_nodes:
            self._store.upsert_nodes(changed_nodes)
        if domain_nodes:
            self._store.upsert_nodes(domain_nodes)
        if domain_edges:
            self._store.upsert_edges(domain_edges)


# ---------------------------------------------------------------------------
# Phase implementations (module-level for testability)
# ---------------------------------------------------------------------------


def _phase1_map_ontology_classes(nodes_map: dict[str, Node]) -> dict[str, Node]:
    """Assign ontology_class to every node based on its NodeType."""
    result: dict[str, Node] = {}
    for node_id, node in nodes_map.items():
        ontology_class = _ONTOLOGY_CLASS.get(node.type)
        if ontology_class is not None and node.ontology_class != ontology_class:
            result[node_id] = node.model_copy(update={"ontology_class": ontology_class})
        else:
            result[node_id] = node
    return result


def _phase2_apply_heuristic_tagging(
    nodes_map: dict[str, Node],
    domains: dict[str, Any],
) -> dict[str, Node]:
    """Tag nodes with domain labels using file_path and fqn patterns from domains config."""
    result = dict(nodes_map)
    for domain_name, raw_cfg in domains.items():
        domain_cfg: dict[str, Any] = raw_cfg or {}
        heuristics: dict[str, Any] = domain_cfg.get("heuristics") or {}
        fp_patterns: list[str] = heuristics.get("file_path_patterns") or []
        fqn_patterns: list[str] = heuristics.get("fqn_patterns") or []

        for node_id, node in nodes_map.items():
            if node.namespace != NodeNamespace.INTERNAL:
                continue
            matched = any(fnmatch.fnmatchcase(node.file_path, p) for p in fp_patterns) or any(
                fnmatch.fnmatchcase(node.id, p) for p in fqn_patterns
            )
            if matched and domain_name not in result[node_id].domains:
                result[node_id] = result[node_id].model_copy(
                    update={"domains": sorted({*result[node_id].domains, domain_name})}
                )
    return result


def _build_children_map(edges: list[Edge]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for edge in edges:
        if edge.type in _STRUCTURAL_EDGE_TYPES:
            children.setdefault(edge.source, []).append(edge.target)
    return children


def _phase3_propagate_domains(
    nodes_map: dict[str, Node],
    edges: list[Edge],
) -> dict[str, Node]:
    """Propagate domains downward through CONTAINS/DECLARES structural edges (O(V+E) BFS)."""
    children = _build_children_map(edges)
    result = dict(nodes_map)
    queue: deque[str] = deque(node_id for node_id, node in result.items() if node.domains)
    in_queue: set[str] = set(queue)

    while queue:
        node_id = queue.popleft()
        in_queue.discard(node_id)
        node = result[node_id]
        for child_id in children.get(node_id, []):
            child = result.get(child_id)
            if child is None:
                continue
            merged = sorted(set(child.domains) | set(node.domains))
            if merged != child.domains:
                result[child_id] = child.model_copy(update={"domains": merged})
                if child_id not in in_queue:
                    queue.append(child_id)
                    in_queue.add(child_id)
    return result


def _collect_cross_domain_pairs(
    src_domains: list[str],
    tgt_domains: list[str],
    dep_pairs: set[tuple[str, str]],
) -> None:
    for src_domain in src_domains:
        for tgt_domain in tgt_domains:
            if src_domain != tgt_domain:
                dep_pairs.add((src_domain, tgt_domain))


def _phase4_infer_domain_dependencies(
    nodes_map: dict[str, Node],
    edges: list[Edge],
) -> tuple[list[Node], list[Edge]]:
    """Emit DOMAIN_DEPENDS_ON edges for every cross-domain CALLS relationship."""
    dep_pairs: set[tuple[str, str]] = set()
    for edge in edges:
        if edge.type != EdgeType.CALLS:
            continue
        src = nodes_map.get(edge.source)
        tgt = nodes_map.get(edge.target)
        if src is None or tgt is None:
            continue
        _collect_cross_domain_pairs(src.domains, tgt.domains, dep_pairs)

    domain_ids: set[str] = set()
    new_edges: list[Edge] = []
    for src_domain, tgt_domain in dep_pairs:
        domain_ids.add(src_domain)
        domain_ids.add(tgt_domain)
        new_edges.append(
            Edge(
                id=f"domain:{src_domain}->domain:{tgt_domain}",
                type=EdgeType.DOMAIN_DEPENDS_ON,
                source=f"domain:{src_domain}",
                target=f"domain:{tgt_domain}",
                confidence=0.8,
            )
        )

    domain_nodes = [
        Node(
            id=f"domain:{name}",
            type=NodeType.DOMAIN_CONCEPT,
            name=name,
            file_path=VIRTUAL_FILE_PATH,
            start_line=0,
            end_line=0,
            namespace=NodeNamespace.INTERNAL,
            ontology_class="DomainConcept",
        )
        for name in domain_ids
    ]
    return domain_nodes, new_edges
