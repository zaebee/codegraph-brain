"""Self-parsing validation for TypeScript: ingest ui/src/ and assert graph structure.

Feeds the TypeScript extractor the UI's own source code and validates
the graph matches the expected structure — the canonical correctness test
for the TS extraction pipeline.
"""

from pathlib import Path

import pytest

from cgis.core.models import Edge, Node
from cgis.extractors.typescript_extractor import file_path_to_module_fqn
from cgis.storage.sqlite_store import SQLiteStore

_TS_SRC = Path(__file__).parent.parent.parent / "ui" / "src"
_skip_no_ui = pytest.mark.skipif(
    not _TS_SRC.exists(),
    reason="ui/src/ not available (requires feat/ui merge)",
)


def _fqn(relative: str, *parts: str) -> str:
    module = file_path_to_module_fqn(relative)
    return ".".join([module, *parts]) if parts else module


@_skip_no_ui
def test_ts_self_parse_completes(ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]]) -> None:
    """Pipeline must complete and produce nodes."""
    _, nodes, _ = ts_graph_data
    assert len(nodes) > 0, "No nodes produced from ui/src/"


@_skip_no_ui
def test_ts_file_nodes_exist(ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]]) -> None:
    """Known .ts and .tsx files must be present as FILE nodes."""
    store, _, _ = ts_graph_data
    expected_files = [
        "App.tsx",
        "hooks/useGraphFilter.ts",
        "hooks/useFlowNavigation.ts",
        "components/ControlPanel.tsx",
        "components/FileContainerNode.tsx",
    ]
    for rel in expected_files:
        fqn = _fqn(rel)
        assert store.get_node(fqn) is not None, f"Missing FILE node: {fqn}"
        assert store.get_node(fqn).type == "FILE", f"{fqn} is not a FILE node"


@_skip_no_ui
def test_ts_function_nodes_exist(ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]]) -> None:
    """Known exported functions must be present as FUNCTION/METHOD nodes."""
    store, _, _ = ts_graph_data
    expected = [
        _fqn("hooks/useGraphFilter.ts", "useGraphFilter"),
        _fqn("hooks/useFlowNavigation.ts", "useFlowNavigation"),
        _fqn("hooks/useSearch.ts", "useSearch"),
        _fqn("hooks/useGraphFetch.ts", "useGraphFetch"),
        _fqn("hooks/useExport.ts", "useExport"),
        _fqn("utils.ts", "aggregateEdges"),
        _fqn("utils.ts", "filterValidEdges"),
    ]
    for fqn in expected:
        assert store.get_node(fqn) is not None, f"Missing function node: {fqn}"


@_skip_no_ui
def test_ts_imports_edge_exists(
    ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """IMPORTS edges must exist in the graph."""
    _, _, resolved_edges = ts_graph_data
    imports_edges = [e for e in resolved_edges if e.type == "IMPORTS"]
    assert len(imports_edges) > 0, "No IMPORTS edges found in the TS graph"


@_skip_no_ui
def test_ts_calls_edge_exists(
    ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """CALLS edges must exist in the graph."""
    _, _, resolved_edges = ts_graph_data
    calls_edges = [e for e in resolved_edges if e.type == "CALLS"]
    assert len(calls_edges) > 0, "No CALLS edges found in the TS graph"


@_skip_no_ui
def test_ts_contains_edge_exists(
    ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """CONTAINS edges must connect FILE nodes to FUNCTION or CLASS children."""
    store, _, resolved_edges = ts_graph_data
    contains_edges = [e for e in resolved_edges if e.type == "CONTAINS"]
    assert len(contains_edges) > 0, "No CONTAINS edges found in the TS graph"

    valid_child_types = {"FUNCTION", "METHOD", "CLASS"}
    structural_contains = [
        e
        for e in contains_edges
        if (src := store.get_node(e.source)) is not None
        and src.type == "FILE"
        and (tgt := store.get_node(e.target)) is not None
        and tgt.type in valid_child_types
    ]
    assert len(structural_contains) > 0, (
        "No CONTAINS edge connects a FILE node to a FUNCTION/METHOD/CLASS node"
    )


@_skip_no_ui
def test_ts_no_absolute_file_paths(
    ts_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """All node file_paths must be relative (no absolute paths)."""
    _, nodes, _ = ts_graph_data
    for node in nodes:
        assert not Path(node.file_path).is_absolute(), (
            f"Node {node.id!r} has absolute file_path: {node.file_path}"
        )
