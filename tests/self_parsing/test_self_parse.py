"""Self-parsing validation: ingest src/cgis/ and assert the resulting graph.

This is the canonical correctness test for the full pipeline
(Extract → Resolve → Store → Query). It feeds the engine its own source code
and checks that the graph matches the actual structure.  Any breaking change
to FQN format, resolver logic, or storage will surface here before unit tests
can catch it.

Expected FQNs are built dynamically with ``file_path_to_module_fqn`` so the
suite stays correct regardless of the absolute checkout path.
"""

from pathlib import Path

import pytest

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeNamespace
from cgis.extractors.python_extractor import file_path_to_module_fqn
from cgis.storage.sqlite_store import SQLiteStore

SRC_DIR = Path(__file__).parent.parent.parent / "src" / "cgis"


def _fqn(relative: str, *parts: str) -> str:
    """Build the expected FQN for a symbol inside SRC_DIR.

    Args:
        relative: path relative to SRC_DIR, e.g. ``"pipeline.py"``
        *parts:   symbol names, e.g. ``"IngestionPipeline"``, ``"run"``

    Returns:
        Fully-qualified node id as stored in the graph.

    After path canonicalization (Issue #33), the pipeline stores paths
    relative to workspace_root (= SRC_DIR when ingesting SRC_DIR directly).
    So ``_fqn("pipeline.py", "IngestionPipeline")`` → ``"pipeline.IngestionPipeline"``.
    """
    module = file_path_to_module_fqn(relative)
    return ".".join([module, *parts]) if parts else module


# ---------------------------------------------------------------------------
# 1. Node existence — known FQNs must be present in the store
# ---------------------------------------------------------------------------

# fmt: off
EXPECTED_NODES = [
    # pipeline
    _fqn("pipeline.py",                    "IngestionPipeline"),
    _fqn("pipeline.py",                    "IngestionPipeline", "run"),
    _fqn("pipeline.py",                    "IngestionPipeline", "_persist_incremental"),
    # query engine
    _fqn("query/engine.py",                "QueryEngine"),
    _fqn("query/engine.py",                "QueryEngine", "get_flow_graph"),
    _fqn("query/engine.py",                "QueryEngine", "get_impact_graph"),
    _fqn("query/engine.py",                "QueryEngine", "_bfs_traverse"),
    # storage
    _fqn("storage/sqlite_store.py",        "SQLiteStore"),
    _fqn("storage/sqlite_store.py",        "SQLiteStore", "save_graph"),
    _fqn("storage/sqlite_store.py",        "SQLiteStore", "save_incremental_batch"),
    # resolver
    _fqn("resolver/engine.py",             "ResolverEngine"),
    _fqn("resolver/engine.py",             "ResolverEngine", "resolve"),
    _fqn("resolver/indices.py",            "SymbolIndex"),
    _fqn("resolver/indices.py",            "IndexBuilder"),
    _fqn("resolver/indices.py",            "IndexBuilder", "build"),
    _fqn("resolver/symbols.py",            "SymbolResolver"),
    _fqn("resolver/symbols.py",            "SymbolResolver", "resolve_global_call"),
    # extractor
    _fqn("extractors/python_extractor.py", "PythonExtractor"),
    _fqn("extractors/python_extractor.py", "PythonExtractor", "parse"),
    _fqn("extractors/python_extractor.py", "file_path_to_module_fqn"),
]
# fmt: on


@pytest.mark.parametrize("fqn", EXPECTED_NODES)
def test_known_node_exists(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]], fqn: str
) -> None:
    """Every known symbol must appear as a node in the graph."""
    store, _, _ = graph_data
    assert store.get_node(fqn) is not None, f"Missing node: {fqn}"


# ---------------------------------------------------------------------------
# 2. Edge existence — known CALLS relationships must be resolved
# ---------------------------------------------------------------------------


def test_pipeline_run_calls_resolver(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """IngestionPipeline.run must have a resolved CALLS edge to ResolverEngine."""
    store, _, _ = graph_data
    run_fqn = _fqn("pipeline.py", "IngestionPipeline", "run")
    resolver_fqn = _fqn("resolver/engine.py", "ResolverEngine")
    targets = {e.target for e in store.get_outgoing_edges(run_fqn)}
    assert resolver_fqn in targets, (
        f"IngestionPipeline.run does not have a resolved call to ResolverEngine.\n"
        f"Outgoing targets: {sorted(t for t in targets if not t.startswith('raw_call:'))}"
    )


def test_get_flow_graph_calls_bfs(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """QueryEngine.get_flow_graph must have a resolved CALLS edge to _bfs_traverse."""
    store, _, _ = graph_data
    flow_fqn = _fqn("query/engine.py", "QueryEngine", "get_flow_graph")
    bfs_fqn = _fqn("query/engine.py", "QueryEngine", "_bfs_traverse")
    targets = {e.target for e in store.get_outgoing_edges(flow_fqn)}
    assert bfs_fqn in targets, (
        f"QueryEngine.get_flow_graph does not call _bfs_traverse.\n"
        f"Outgoing targets: {sorted(t for t in targets if not t.startswith('raw_call:'))}"
    )


def test_get_impact_graph_calls_bfs(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """QueryEngine.get_impact_graph must also resolve to _bfs_traverse."""
    store, _, _ = graph_data
    impact_fqn = _fqn("query/engine.py", "QueryEngine", "get_impact_graph")
    bfs_fqn = _fqn("query/engine.py", "QueryEngine", "_bfs_traverse")
    targets = {e.target for e in store.get_outgoing_edges(impact_fqn)}
    assert bfs_fqn in targets


# ---------------------------------------------------------------------------
# 3. No phantom bleed — all nodes must come from files under SRC_DIR
# ---------------------------------------------------------------------------


def test_no_phantom_bleed(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """All node file_paths must be relative (no absolute paths from the host machine)."""
    _, nodes, _ = graph_data
    for node in nodes:
        assert not Path(node.file_path).is_absolute(), (
            f"Node {node.id!r} has absolute file_path — path canonicalization failed: "
            f"{node.file_path}"
        )


def test_no_external_library_nodes(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """External library symbols appear only as virtual nodes (namespace=EXTERNAL).

    They must not be mistaken for internal source nodes — no real source location.
    """
    store, _, _ = graph_data
    external_fqns = [
        "pydantic.BaseModel",
        "tree_sitter.Parser",
        "structlog.getLogger",
        "typer.Typer",
    ]
    for fqn in external_fqns:
        node = store.get_node(fqn)
        if node is not None:
            assert node.namespace == NodeNamespace.EXTERNAL, (
                f"{fqn} present but not classified as EXTERNAL"
            )
            assert node.file_path == VIRTUAL_FILE_PATH, (
                f"{fqn} has unexpected file_path={node.file_path!r}"
            )


# ---------------------------------------------------------------------------
# 4. File coverage — every non-empty .py file must produce at least one node
# ---------------------------------------------------------------------------


def test_file_coverage(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Every .py file in src/cgis/ that defines symbols must have at least one node.

    Empty files and bare ``__init__.py`` stubs are excluded — the extractor
    only emits nodes for functions and classes.
    """
    _, nodes, _ = graph_data
    files_with_nodes = {n.file_path for n in nodes}

    for py_file in sorted(SRC_DIR.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8").strip()
        # Skip files that have no function/class definitions
        if not any(kw in source for kw in ("def ", "class ")):
            continue
        rel_path = py_file.relative_to(SRC_DIR).as_posix()
        assert rel_path in files_with_nodes, (
            f"{py_file.relative_to(SRC_DIR.parent.parent)} has definitions "
            f"but produced no nodes in the graph"
        )


# ---------------------------------------------------------------------------
# 5. Symbol-import edges — #161 slice 2 self-parse pins
# ---------------------------------------------------------------------------


def test_engine_imports_symbolresolver_symbol_level(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """resolver.engine must carry an IMPORTS_SYMBOL edge to SymbolResolver (#161 slice 2 pin)."""
    store, _, _ = graph_data
    engine_module = _fqn("resolver/engine.py")
    symbol_resolver = _fqn("resolver/symbols.py", "SymbolResolver")
    edges = store.get_outgoing_edges(engine_module)
    assert any(e.target == symbol_resolver and e.type == EdgeType.IMPORTS_SYMBOL for e in edges), (
        f"Missing IMPORTS_SYMBOL pin; outgoing: {sorted({e.target for e in edges})}"
    )


def test_no_raw_import_leak_in_self_graph(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """raw_import: must never survive resolution (#161 slice 2 no-leak, spec §3)."""
    _, _, edges = graph_data
    leaked = [e for e in edges if e.target.startswith("raw_import:")]
    assert leaked == [], f"raw_import: leaked: {[(e.source, e.target) for e in leaked]}"
