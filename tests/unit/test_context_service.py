"""Unit tests for the GraphRAG context orchestration service (#19)."""

from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.context.context_service import build_context
from cgis.storage.sqlite_store import SQLiteStore


def _graph() -> tuple[list[Node], list[Edge]]:
    """A class Engine.run that is called by cli.main, calls helper + an unresolved name."""
    nodes = [
        Node(
            id="app.svc",
            type=NodeType.FILE,
            name="svc",
            file_path="app/svc.py",
            start_line=1,
            end_line=20,
        ),
        Node(
            id="app.svc.Engine",
            type=NodeType.CLASS,
            name="Engine",
            file_path="app/svc.py",
            start_line=1,
            end_line=10,
        ),
        Node(
            id="app.svc.Engine.run",
            type=NodeType.METHOD,
            name="run",
            file_path="app/svc.py",
            start_line=2,
            end_line=3,
        ),
        Node(
            id="app.svc.Engine.helper",
            type=NodeType.METHOD,
            name="helper",
            file_path="app/svc.py",
            start_line=5,
            end_line=6,
        ),
        Node(
            id="app.cli.main",
            type=NodeType.FUNCTION,
            name="main",
            file_path="app/cli.py",
            start_line=8,
            end_line=12,
        ),
    ]
    edges = [
        Edge(id="e1", source="app.svc", target="app.svc.Engine", type=EdgeType.CONTAINS),
        Edge(id="e2", source="app.svc.Engine", target="app.svc.Engine.run", type=EdgeType.DECLARES),
        Edge(
            id="e3", source="app.svc.Engine", target="app.svc.Engine.helper", type=EdgeType.DECLARES
        ),
        Edge(id="e4", source="app.cli.main", target="app.svc.Engine.run", type=EdgeType.CALLS),
        Edge(
            id="e5",
            source="app.svc.Engine.run",
            target="app.svc.Engine.helper",
            type=EdgeType.CALLS,
        ),
        Edge(
            id="e6",
            source="app.svc.Engine.run",
            target="raw_call:external",
            type=EdgeType.CALLS,
            confidence=0.1,
        ),
    ]
    return nodes, edges


def _store(tmp_path: Path) -> str:
    db = str(tmp_path / "g.db")
    nodes, edges = _graph()
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def test_build_context_assembles_full_package(tmp_path: Path) -> None:
    """The package carries header, the resolved callers, callees and class siblings."""
    db = _store(tmp_path)
    with SQLiteStore(db) as store:
        out = build_context(store, "app.svc.Engine.run", depth=1)

    assert 'focal="app.svc.Engine.run"' in out
    assert "- app.cli.main (FUNCTION, cli.py:8)" in out  # caller
    assert "- app.svc.Engine.helper (METHOD, svc.py:5)" in out  # callee
    assert '<class name="app.svc.Engine"' in out
    # helper is a sibling; run (the focus) must not list itself as its own sibling
    assert "- helper (METHOD, svc.py:5)" in out
    assert "- run (" not in out


def test_unresolved_callee_is_surfaced(tmp_path: Path) -> None:
    """A raw_call: dependency that never resolved is listed as unresolved."""
    db = _store(tmp_path)
    with SQLiteStore(db) as store:
        out = build_context(store, "app.svc.Engine.run", depth=1)
    assert "- external (unresolved)" in out


def test_module_level_function_has_no_class(tmp_path: Path) -> None:
    """A function whose parent is a FILE renders the standalone-function note."""
    db = _store(tmp_path)
    with SQLiteStore(db) as store:
        out = build_context(store, "app.cli.main", depth=1)
    assert "none — module-level function" in out


def test_snippet_pulled_from_source_root(tmp_path: Path) -> None:
    """When source_root locates the file on disk, the focal source is inlined."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "svc.py").write_text(
        "class Engine:\n"
        "    def run(self):\n"
        "        return self.helper()\n"
        "    # pad\n"
        "    def helper(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    db = _store(tmp_path)
    with SQLiteStore(db) as store:
        out = build_context(store, "app.svc.Engine.run", depth=1, source_root=str(tmp_path))
    assert "def run(self):" in out
    assert "```python" in out


def test_missing_focus_raises_value_error(tmp_path: Path) -> None:
    """An FQN absent from the graph is a programming error, surfaced as ValueError."""
    db = _store(tmp_path)
    with SQLiteStore(db) as store, pytest.raises(ValueError, match="not found"):
        build_context(store, "app.svc.Nope", depth=1)


def test_external_callees_are_excluded(tmp_path: Path) -> None:
    """Builtins / third-party (EXTERNAL namespace) are noise for an agent — only INTERNAL shows."""
    nodes = [
        Node(
            id="app.f",
            type=NodeType.FUNCTION,
            name="f",
            file_path="app.py",
            start_line=1,
            end_line=5,
        ),
        Node(
            id="app.g",
            type=NodeType.FUNCTION,
            name="g",
            file_path="app.py",
            start_line=7,
            end_line=9,
        ),
        Node(
            id="builtins.len",
            type=NodeType.FUNCTION,
            name="len",
            file_path="EXTERNAL",
            start_line=0,
            end_line=0,
            namespace=NodeNamespace.EXTERNAL,
        ),
    ]
    edges = [
        Edge(id="c1", source="app.f", target="app.g", type=EdgeType.CALLS),
        Edge(id="c2", source="app.f", target="builtins.len", type=EdgeType.CALLS),
    ]
    db = str(tmp_path / "ext.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    with SQLiteStore(db) as store:
        out = build_context(store, "app.f", depth=1)
    assert "app.g" in out
    assert "builtins.len" not in out


def test_windows_style_file_path_is_normalized_for_snippet(tmp_path: Path) -> None:
    """A backslash file_path (Windows-ingested graph) still locates the snippet on POSIX."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text("def w():\n    return 1\n", encoding="utf-8")
    node = Node(
        id="pkg.m.w",
        type=NodeType.FUNCTION,
        name="w",
        file_path="pkg\\m.py",  # backslash, as a Windows ingest would store
        start_line=1,
        end_line=2,
    )
    db = str(tmp_path / "win.db")
    with SQLiteStore(db) as store:
        store.save_graph([node], [])
    with SQLiteStore(db) as store:
        out = build_context(store, "pkg.m.w", source_root=str(tmp_path))
    assert "def w():" in out


def test_structural_parent_prefers_class_over_file(tmp_path: Path) -> None:
    """When a method has both a FILE and a CLASS structural parent, the CLASS wins."""
    nodes = [
        Node(id="m", type=NodeType.FILE, name="m", file_path="m.py", start_line=1, end_line=9),
        Node(id="m.C", type=NodeType.CLASS, name="C", file_path="m.py", start_line=1, end_line=9),
        Node(
            id="m.C.meth",
            type=NodeType.METHOD,
            name="meth",
            file_path="m.py",
            start_line=2,
            end_line=3,
        ),
    ]
    edges = [
        # FILE→method edge listed FIRST so the naive "first structural edge" would pick the file
        Edge(id="x1", source="m", target="m.C.meth", type=EdgeType.CONTAINS),
        Edge(id="x2", source="m.C", target="m.C.meth", type=EdgeType.DECLARES),
    ]
    db = str(tmp_path / "p.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    with SQLiteStore(db) as store:
        out = build_context(store, "m.C.meth", depth=1)
    assert '<class name="m.C"' in out


def test_transitive_unresolved_surfaces_at_depth_2(tmp_path: Path) -> None:
    """At depth 2 a callee's own unresolved call also surfaces (symmetric with resolved)."""
    nodes = [
        Node(
            id="a.f", type=NodeType.FUNCTION, name="f", file_path="a.py", start_line=1, end_line=2
        ),
        Node(
            id="a.mid",
            type=NodeType.FUNCTION,
            name="mid",
            file_path="a.py",
            start_line=4,
            end_line=5,
        ),
    ]
    edges = [
        Edge(id="r1", source="a.f", target="a.mid", type=EdgeType.CALLS),
        Edge(id="r2", source="a.mid", target="raw_call:deep", type=EdgeType.CALLS, confidence=0.1),
    ]
    db = str(tmp_path / "t.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    with SQLiteStore(db) as store:
        shallow = build_context(store, "a.f", depth=1)
        deep = build_context(store, "a.f", depth=2)
    assert "deep (unresolved)" not in shallow  # mid's unresolved is 2 hops away
    assert "deep (unresolved)" in deep
