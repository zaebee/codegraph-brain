"""Unit tests for the SemanticUpliftEngine (src/cgis/resolver/uplift.py)."""

from collections.abc import Generator
from pathlib import Path

import pytest

from cgis.core.models import (
    VIRTUAL_FILE_PATH,
    Edge,
    EdgeType,
    Node,
    NodeNamespace,
    NodeType,
)
from cgis.resolver.uplift import (
    SemanticUpliftEngine,
    _phase1_map_ontology_classes,
    _phase2_apply_heuristic_tagging,
    _phase3_propagate_domains,
    _phase4_infer_domain_dependencies,
)
from cgis.storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(
    node_id: str,
    node_type: NodeType = NodeType.FUNCTION,
    file_path: str = "mod.py",
    namespace: NodeNamespace = NodeNamespace.INTERNAL,
    domains: list[str] | None = None,
) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=2,
        namespace=namespace,
        domains=domains or [],
    )


def _edge(
    source: str,
    target: str,
    edge_type: EdgeType = EdgeType.CALLS,
) -> Edge:
    return Edge(id=f"{source}->{target}", source=source, target=target, type=edge_type)


# ---------------------------------------------------------------------------
# Phase 1 — Ontology class mapping
# ---------------------------------------------------------------------------


def test_phase1_maps_class_node() -> None:
    n = _node("pkg.MyClass", NodeType.CLASS)
    result = _phase1_map_ontology_classes({"pkg.MyClass": n})
    assert result["pkg.MyClass"].ontology_class == "Class"


def test_phase1_maps_method_node() -> None:
    n = _node("pkg.MyClass.do", NodeType.METHOD)
    result = _phase1_map_ontology_classes({"pkg.MyClass.do": n})
    assert result["pkg.MyClass.do"].ontology_class == "Method"


def test_phase1_maps_all_node_types() -> None:
    expected = {
        NodeType.FILE: "File",
        NodeType.MODULE: "Module",
        NodeType.CLASS: "Class",
        NodeType.FUNCTION: "Function",
        NodeType.METHOD: "Method",
        NodeType.API_ENDPOINT: "APIEndpoint",
        NodeType.DB_TABLE: "DBTable",
        NodeType.DOMAIN_CONCEPT: "DomainConcept",
    }
    for node_type, ontology_class in expected.items():
        n = _node("x", node_type)
        result = _phase1_map_ontology_classes({"x": n})
        assert result["x"].ontology_class == ontology_class, (
            f"{node_type} → expected {ontology_class!r}"
        )


def test_phase1_overwrites_existing_ontology_class() -> None:
    n = Node(
        id="x",
        type=NodeType.CLASS,
        name="x",
        file_path="mod.py",
        start_line=1,
        end_line=2,
        ontology_class="CustomClass",
    )
    result = _phase1_map_ontology_classes({"x": n})
    # existing value gets replaced with canonical mapping
    assert result["x"].ontology_class == "Class"


# ---------------------------------------------------------------------------
# Phase 2 — Heuristic domain tagging
# ---------------------------------------------------------------------------

_SAMPLE_DOMAINS = {
    "StorageLayer": {
        "heuristics": {
            "file_path_patterns": ["storage/*"],
            "fqn_patterns": ["*.Store*"],
        }
    },
    "ParserLayer": {
        "heuristics": {
            "file_path_patterns": ["extractors/*"],
            "fqn_patterns": [],
        }
    },
}


def test_phase2_tags_by_file_path_pattern() -> None:
    n = _node("storage.sqlite_store.SQLiteStore", file_path="storage/sqlite_store.py")
    result = _phase2_apply_heuristic_tagging(
        {"storage.sqlite_store.SQLiteStore": n}, _SAMPLE_DOMAINS
    )
    assert "StorageLayer" in result["storage.sqlite_store.SQLiteStore"].domains


def test_phase2_tags_by_fqn_pattern() -> None:
    n = _node("pkg.StoreImpl", file_path="some_other.py")
    result = _phase2_apply_heuristic_tagging({"pkg.StoreImpl": n}, _SAMPLE_DOMAINS)
    assert "StorageLayer" in result["pkg.StoreImpl"].domains


def test_phase2_skips_external_nodes() -> None:
    n = _node("ext.Store", file_path=VIRTUAL_FILE_PATH, namespace=NodeNamespace.EXTERNAL)
    result = _phase2_apply_heuristic_tagging({"ext.Store": n}, _SAMPLE_DOMAINS)
    assert result["ext.Store"].domains == []


def test_phase2_does_not_duplicate_domains() -> None:
    n = _node("storage.Store", file_path="storage/store.py", domains=["StorageLayer"])
    result = _phase2_apply_heuristic_tagging({"storage.Store": n}, _SAMPLE_DOMAINS)
    assert result["storage.Store"].domains.count("StorageLayer") == 1


def test_phase2_accumulates_multiple_domains() -> None:
    n = _node("extractors.StoreImpl", file_path="extractors/store.py")
    result = _phase2_apply_heuristic_tagging({"extractors.StoreImpl": n}, _SAMPLE_DOMAINS)
    domains = result["extractors.StoreImpl"].domains
    assert "ParserLayer" in domains
    assert "StorageLayer" in domains


# ---------------------------------------------------------------------------
# Phase 3 — Structural propagation
# ---------------------------------------------------------------------------


def test_phase3_propagates_domain_to_child() -> None:
    parent = _node("mod", NodeType.FILE, domains=["StorageLayer"])
    child = _node("mod.MyClass", NodeType.CLASS)
    nodes_map = {"mod": parent, "mod.MyClass": child}
    edges = [_edge("mod", "mod.MyClass", EdgeType.CONTAINS)]

    result = _phase3_propagate_domains(nodes_map, edges)
    assert "StorageLayer" in result["mod.MyClass"].domains


def test_phase3_propagates_transitively() -> None:
    file_node = _node("mod", NodeType.FILE, domains=["StorageLayer"])
    cls_node = _node("mod.Cls", NodeType.CLASS)
    method_node = _node("mod.Cls.do", NodeType.METHOD)
    nodes_map = {"mod": file_node, "mod.Cls": cls_node, "mod.Cls.do": method_node}
    edges = [
        _edge("mod", "mod.Cls", EdgeType.CONTAINS),
        _edge("mod.Cls", "mod.Cls.do", EdgeType.DECLARES),
    ]

    result = _phase3_propagate_domains(nodes_map, edges)
    assert "StorageLayer" in result["mod.Cls.do"].domains


def test_phase3_does_not_propagate_calls_edges() -> None:
    caller = _node("a.fn", domains=["DomainA"])
    callee = _node("b.fn")
    nodes_map = {"a.fn": caller, "b.fn": callee}
    edges = [_edge("a.fn", "b.fn", EdgeType.CALLS)]

    result = _phase3_propagate_domains(nodes_map, edges)
    assert result["b.fn"].domains == []


# ---------------------------------------------------------------------------
# Phase 4 — Domain dependency inference
# ---------------------------------------------------------------------------


def test_phase4_emits_domain_depends_on_edge() -> None:
    src = _node("a.fn", domains=["ParserLayer"])
    tgt = _node("b.fn", domains=["StorageLayer"])
    nodes_map = {"a.fn": src, "b.fn": tgt}
    edges = [_edge("a.fn", "b.fn", EdgeType.CALLS)]

    _domain_nodes, domain_edges = _phase4_infer_domain_dependencies(nodes_map, edges)

    edge_types = {e.type for e in domain_edges}
    assert EdgeType.DOMAIN_DEPENDS_ON in edge_types
    sources = {e.source for e in domain_edges}
    targets = {e.target for e in domain_edges}
    assert "domain:ParserLayer" in sources
    assert "domain:StorageLayer" in targets


def test_phase4_creates_domain_concept_nodes() -> None:
    src = _node("a.fn", domains=["ParserLayer"])
    tgt = _node("b.fn", domains=["StorageLayer"])
    nodes_map = {"a.fn": src, "b.fn": tgt}
    edges = [_edge("a.fn", "b.fn", EdgeType.CALLS)]

    domain_nodes, _ = _phase4_infer_domain_dependencies(nodes_map, edges)

    node_ids = {n.id for n in domain_nodes}
    assert "domain:ParserLayer" in node_ids
    assert "domain:StorageLayer" in node_ids
    for n in domain_nodes:
        assert n.type == NodeType.DOMAIN_CONCEPT
        assert n.ontology_class == "DomainConcept"


def test_phase4_skips_same_domain_calls() -> None:
    src = _node("a.fn", domains=["StorageLayer"])
    tgt = _node("b.fn", domains=["StorageLayer"])
    nodes_map = {"a.fn": src, "b.fn": tgt}
    edges = [_edge("a.fn", "b.fn", EdgeType.CALLS)]

    _, domain_edges = _phase4_infer_domain_dependencies(nodes_map, edges)
    assert domain_edges == []


def test_phase4_ignores_non_calls_edges() -> None:
    src = _node("a.fn", domains=["ParserLayer"])
    tgt = _node("b.fn", domains=["StorageLayer"])
    nodes_map = {"a.fn": src, "b.fn": tgt}
    edges = [_edge("a.fn", "b.fn", EdgeType.IMPORTS)]

    _, domain_edges = _phase4_infer_domain_dependencies(nodes_map, edges)
    assert domain_edges == []


# ---------------------------------------------------------------------------
# Integration — execute_uplift via real SQLiteStore
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_store(tmp_path: Path) -> Generator[SQLiteStore, None, None]:
    with SQLiteStore(str(tmp_path / "test.db")) as store:
        yield store


def test_execute_uplift_sets_ontology_class(temp_store: SQLiteStore) -> None:
    nodes = [_node("pkg.MyClass", NodeType.CLASS)]
    temp_store.save_graph(nodes, [])

    SemanticUpliftEngine(temp_store).execute_uplift()

    result = temp_store.get_node("pkg.MyClass")
    assert result is not None
    assert result.ontology_class == "Class"


def test_execute_uplift_with_domains_config(temp_store: SQLiteStore, tmp_path: Path) -> None:
    domains_yaml = tmp_path / "domains.yaml"
    domains_yaml.write_text(
        "version: '0.1.0'\ndomains:\n"
        "  StorageLayer:\n"
        "    heuristics:\n"
        "      file_path_patterns: ['storage/*']\n"
        "      fqn_patterns: []\n",
        encoding="utf-8",
    )
    nodes = [_node("storage.Store", file_path="storage/store.py")]
    temp_store.save_graph(nodes, [])

    SemanticUpliftEngine(temp_store, str(domains_yaml)).execute_uplift()

    result = temp_store.get_node("storage.Store")
    assert result is not None
    assert "StorageLayer" in result.domains


def test_execute_uplift_creates_domain_concept_nodes(
    temp_store: SQLiteStore, tmp_path: Path
) -> None:
    domains_yaml = tmp_path / "domains.yaml"
    domains_yaml.write_text(
        "version: '0.1.0'\ndomains:\n"
        "  Alpha:\n    heuristics:\n      file_path_patterns: ['a.py']\n      fqn_patterns: []\n"
        "  Beta:\n    heuristics:\n      file_path_patterns: ['b.py']\n      fqn_patterns: []\n",
        encoding="utf-8",
    )
    nodes = [
        _node("a.fn", file_path="a.py"),
        _node("b.fn", file_path="b.py"),
    ]
    edges = [_edge("a.fn", "b.fn", EdgeType.CALLS)]
    temp_store.save_graph(nodes, edges)

    SemanticUpliftEngine(temp_store, str(domains_yaml)).execute_uplift()

    alpha_node = temp_store.get_node("domain:Alpha")
    beta_node = temp_store.get_node("domain:Beta")
    assert alpha_node is not None
    assert alpha_node.type == NodeType.DOMAIN_CONCEPT
    assert beta_node is not None
    assert beta_node.type == NodeType.DOMAIN_CONCEPT

    all_edges = temp_store.get_all_edges()
    dep_edges = [e for e in all_edges if e.type == EdgeType.DOMAIN_DEPENDS_ON]
    assert len(dep_edges) == 1
    assert dep_edges[0].source == "domain:Alpha"
    assert dep_edges[0].target == "domain:Beta"


def test_execute_uplift_without_domains_clears_stale_tags(
    temp_store: SQLiteStore, tmp_path: Path
) -> None:
    """Re-running uplift without --domains removes previously assigned domain tags."""
    domains_yaml = tmp_path / "domains.yaml"
    domains_yaml.write_text(
        "version: '0.1.0'\ndomains:\n"
        "  Alpha:\n    heuristics:\n      file_path_patterns: ['a.py']\n      fqn_patterns: []\n"
        "  Beta:\n    heuristics:\n      file_path_patterns: ['b.py']\n      fqn_patterns: []\n",
        encoding="utf-8",
    )
    nodes = [_node("a.fn", file_path="a.py"), _node("b.fn", file_path="b.py")]
    edges = [_edge("a.fn", "b.fn", EdgeType.CALLS)]
    temp_store.save_graph(nodes, edges)

    # First run: assign domain tags and create DOMAIN_CONCEPT nodes
    SemanticUpliftEngine(temp_store, str(domains_yaml)).execute_uplift()
    assert temp_store.get_node("domain:Alpha") is not None
    tagged = temp_store.get_node("a.fn")
    assert tagged is not None
    assert "Alpha" in tagged.domains

    # Second run: no domains config — stale tags and concept nodes must be cleared
    SemanticUpliftEngine(temp_store).execute_uplift()

    assert temp_store.get_node("domain:Alpha") is None
    cleared = temp_store.get_node("a.fn")
    assert cleared is not None
    assert cleared.domains == []
