"""Unit tests for the guardian chunker (spec: 2026-06-11-guardian-chunker-design.md)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.guardian.chunker import Chunk, build_chunks, split_diff_by_file
from cgis.storage.sqlite_store import SQLiteStore


def fdiff(path: str, body: str = "+x = 1") -> str:
    """One minimal single-hunk diff block for `path`."""
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n{body}\n"


def test_split_two_files() -> None:
    """Two blocks come back keyed by their new paths, content intact."""
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py", "+y = 2")
    blocks = split_diff_by_file(diff)
    assert set(blocks) == {"src/cgis/a.py", "src/cgis/b.py"}
    assert "+x = 1" in blocks["src/cgis/a.py"]
    assert "+y = 2" in blocks["src/cgis/b.py"]
    assert "+y = 2" not in blocks["src/cgis/a.py"]


def test_split_empty_diff() -> None:
    """Empty input yields no blocks."""
    assert split_diff_by_file("") == {}


def test_split_deletion_keyed_by_old_path() -> None:
    """+++ /dev/null → block keyed by the OLD path; deletions stay reviewable."""
    diff = (
        "diff --git a/src/cgis/gone.py b/src/cgis/gone.py\n"
        "--- a/src/cgis/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-x = 1\n"
    )
    assert set(split_diff_by_file(diff)) == {"src/cgis/gone.py"}


def test_split_rename_keyed_by_new_path() -> None:
    """Renames are keyed by the new path — consistent with Finding.file."""
    diff = (
        "diff --git a/src/cgis/old.py b/src/cgis/new.py\n"
        "--- a/src/cgis/old.py\n"
        "+++ b/src/cgis/new.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    assert set(split_diff_by_file(diff)) == {"src/cgis/new.py"}


def test_split_diff_text_embedded_in_diff() -> None:
    """Added lines containing '+diff --git ...' must not start a new block."""
    diff = (
        "diff --git a/tests/unit/test_x.py b/tests/unit/test_x.py\n"
        "--- a/tests/unit/test_x.py\n"
        "+++ b/tests/unit/test_x.py\n"
        "@@ -0,0 +2 @@\n"
        '+DIFF = "diff --git a/inner.py b/inner.py"\n'
        "++++ b/inner.py\n"
    )
    blocks = split_diff_by_file(diff)
    assert set(blocks) == {"tests/unit/test_x.py"}


def test_split_binary_block_keyed_via_git_header() -> None:
    """Binary blocks have no ---/+++ headers; fall back to the b/ side of diff --git."""
    diff = "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n"
    assert set(split_diff_by_file(diff)) == {"logo.png"}


def test_split_quoted_binary_block_keyed_via_git_header() -> None:
    """Git-quoted binary header (`"b/logo 2.png"`) — the quote precedes b/."""
    diff = (
        'diff --git "a/img/logo 2.png" "b/img/logo 2.png"\n'
        'Binary files "a/img/logo 2.png" and "b/img/logo 2.png" differ\n'
    )
    assert set(split_diff_by_file(diff)) == {"img/logo 2.png"}


def test_split_quoted_text_headers() -> None:
    """Quoted ---/+++ headers yield the bare path: no quotes, no b/ prefix."""
    diff = (
        'diff --git "a/src/x y.py" "b/src/x y.py"\n'
        '--- "a/src/x y.py"\n'
        '+++ "b/src/x y.py"\n'
        "@@ -0,0 +1 @@\n"
        "+x = 1\n"
    )
    assert set(split_diff_by_file(diff)) == {"src/x y.py"}


def test_split_quoted_deletion_keyed_by_old_path() -> None:
    """Quoted old header + /dev/null new side → keyed by the unquoted old path."""
    diff = (
        'diff --git "a/old name.py" "b/old name.py"\n'
        '--- "a/old name.py"\n'
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-x = 1\n"
    )
    assert set(split_diff_by_file(diff)) == {"old name.py"}


def test_split_unparsable_block_skipped() -> None:
    """A block with no parsable path is skipped (logged), never raised on (spec §5)."""
    assert split_diff_by_file("diff --git\ngarbage\n") == {}


def test_build_chunks_no_store_isolated() -> None:
    """store=None → every file is its own chunk, sorted by path."""
    diff = fdiff("src/cgis/b.py") + fdiff("src/cgis/a.py")
    chunks = build_chunks(diff, store=None)
    assert [c.files for c in chunks] == [("src/cgis/a.py",), ("src/cgis/b.py",)]
    assert "+x = 1" in chunks[0].diff


def test_build_chunks_empty_diff() -> None:
    """Empty diff → no chunks."""
    assert build_chunks("", store=None) == []


def test_chunk_is_frozen() -> None:
    """Chunk follows the project's immutable-model convention."""
    chunk = Chunk(files=("a.py",), diff="d")
    with pytest.raises(ValidationError):
        chunk.diff = "x"  # type: ignore[misc]


def _node(fqn: str, file_path: str) -> Node:
    """Minimal MODULE node for graph fixtures."""
    return Node(
        id=fqn,
        type=NodeType.MODULE,
        name=fqn.rsplit(".", 1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=1,
    )


def _edge(source: str, target: str, etype: EdgeType = EdgeType.IMPORTS) -> Edge:
    """Minimal edge for graph fixtures."""
    return Edge(id=f"{source}->{target}:{etype}", source=source, target=target, type=etype)


def _make_store(tmp_path: Path, nodes: list[Node], edges: list[Edge]) -> SQLiteStore:
    """Persist a small synthetic graph and return the connected store."""
    store = SQLiteStore(str(tmp_path / "graph.db"))
    store.connect()
    store.save_graph(nodes, edges)
    return store


def test_imports_edge_joins_two_files(tmp_path: Path) -> None:
    """a.py imports b.py, both changed → one two-file chunk."""
    store = _make_store(
        tmp_path,
        [_node("src.cgis.a", "src/cgis/a.py"), _node("src.cgis.b", "src/cgis/b.py")],
        [_edge("src.cgis.a", "src.cgis.b")],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    chunks = build_chunks(diff, store)
    assert [c.files for c in chunks] == [("src/cgis/a.py", "src/cgis/b.py")]
    assert "+x = 1" in chunks[0].diff


def test_calls_edge_joins_symbol_level_nodes(tmp_path: Path) -> None:
    """CALLS between function nodes connects their FILES via node.file_path."""
    store = _make_store(
        tmp_path,
        [_node("src.cgis.a.run", "src/cgis/a.py"), _node("src.cgis.b.go", "src/cgis/b.py")],
        [_edge("src.cgis.a.run", "src.cgis.b.go", EdgeType.CALLS)],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    assert [c.files for c in build_chunks(diff, store)] == [("src/cgis/a.py", "src/cgis/b.py")]


def test_indirect_path_through_unchanged_file_does_not_join(tmp_path: Path) -> None:
    """A→X→B with only A and B changed → A and B stay separate (induced subgraph)."""
    store = _make_store(
        tmp_path,
        [
            _node("src.cgis.a", "src/cgis/a.py"),
            _node("src.cgis.x", "src/cgis/x.py"),
            _node("src.cgis.b", "src/cgis/b.py"),
        ],
        [_edge("src.cgis.a", "src.cgis.x"), _edge("src.cgis.x", "src.cgis.b")],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")  # x.py NOT in the diff
    assert [c.files for c in build_chunks(diff, store)] == [
        ("src/cgis/a.py",),
        ("src/cgis/b.py",),
    ]


def test_non_chunk_edge_types_ignored(tmp_path: Path) -> None:
    """CONTAINS/DECLARES etc. carry structure, not coupling — they must not join."""
    store = _make_store(
        tmp_path,
        [_node("src.cgis.a", "src/cgis/a.py"), _node("src.cgis.b", "src/cgis/b.py")],
        [_edge("src.cgis.a", "src.cgis.b", EdgeType.CONTAINS)],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    assert len(build_chunks(diff, store)) == 2


def test_source_root_normalization(tmp_path: Path) -> None:
    """Graph paths are ingest-root-relative (cgis/...), diff paths repo-relative (src/cgis/...)."""
    store = _make_store(
        tmp_path,
        [_node("cgis.a", "cgis/a.py"), _node("cgis.b", "cgis/b.py")],
        [_edge("cgis.a", "cgis.b")],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    chunks = build_chunks(diff, store, source_root="src")
    assert [c.files for c in chunks] == [("src/cgis/a.py", "src/cgis/b.py")]


def test_raw_call_target_skipped(tmp_path: Path) -> None:
    """Unresolved raw_call: targets have no node and must not crash or join."""
    store = _make_store(
        tmp_path,
        [_node("src.cgis.a", "src/cgis/a.py"), _node("src.cgis.b", "src/cgis/b.py")],
        [_edge("src.cgis.a", "raw_call:mystery", EdgeType.CALLS)],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    assert len(build_chunks(diff, store)) == 2


def test_test_file_attaches_to_its_module(tmp_path: Path) -> None:
    """tests/unit/test_guardian_core.py joins the chunk of src/cgis/guardian/core.py."""
    store = _make_store(
        tmp_path, [_node("src.cgis.guardian.core", "src/cgis/guardian/core.py")], []
    )
    diff = fdiff("src/cgis/guardian/core.py") + fdiff("tests/unit/test_guardian_core.py")
    chunks = build_chunks(diff, store)
    assert [c.files for c in chunks] == [
        ("src/cgis/guardian/core.py", "tests/unit/test_guardian_core.py")
    ]


def test_multiple_tests_attach_to_same_chunk() -> None:
    """Two test files pairing with the same module land in one chunk."""
    diff = (
        fdiff("src/cgis/guardian/core.py")
        + fdiff("tests/unit/test_core.py")
        + fdiff("tests/unit/test_guardian_core.py")
    )
    chunks = build_chunks(diff, store=None)
    assert [c.files for c in chunks] == [
        (
            "src/cgis/guardian/core.py",
            "tests/unit/test_core.py",
            "tests/unit/test_guardian_core.py",
        )
    ]


def test_test_pairing_needs_underscore_boundary() -> None:
    """'core' must not match score.py — suffix only counts at an underscore boundary."""
    diff = fdiff("src/cgis/score.py") + fdiff("tests/unit/test_core.py")
    chunks = build_chunks(diff, store=None)
    assert [c.files for c in chunks] == [
        ("src/cgis/score.py",),
        ("tests/unit/test_core.py",),
    ]


def test_ambiguous_test_pairing_stays_isolated() -> None:
    """test_engine.py with two engine.py candidates changed → isolated."""
    diff = (
        fdiff("src/cgis/resolver/engine.py")
        + fdiff("src/cgis/query/engine.py")
        + fdiff("tests/unit/test_engine.py")
    )
    chunks = build_chunks(diff, store=None)
    assert ("tests/unit/test_engine.py",) in [c.files for c in chunks]


def test_non_tests_dir_file_not_paired() -> None:
    """Only files under tests/ participate in pairing (spec §4.2.5)."""
    diff = fdiff("scripts/test_helper.py") + fdiff("src/cgis/helper.py")
    chunks = build_chunks(diff, store=None)
    assert len(chunks) == 2


def test_broken_store_degrades_to_isolated_chunks() -> None:
    """A store that raises on read → isolated chunks, no exception escapes."""

    class _BrokenStore(SQLiteStore):
        def get_nodes_by_file(self, file_path: str) -> list[Node]:  # noqa: ARG002
            """Simulate a corrupt db on the first read _graph_pairs performs."""
            msg = "corrupt db"
            raise RuntimeError(msg)

    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    chunks = build_chunks(diff, _BrokenStore(":memory:"))
    assert [c.files for c in chunks] == [("src/cgis/a.py",), ("src/cgis/b.py",)]


def test_build_chunks_deterministic(tmp_path: Path) -> None:
    """Same inputs twice → byte-identical output."""
    store = _make_store(
        tmp_path,
        [_node("src.cgis.a", "src/cgis/a.py"), _node("src.cgis.b", "src/cgis/b.py")],
        [_edge("src.cgis.a", "src.cgis.b")],
    )
    diff = fdiff("src/cgis/c.py") + fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    assert build_chunks(diff, store) == build_chunks(diff, store)


def test_split_duplicate_path_blocks_merged() -> None:
    """Two blocks for the same path merge losslessly instead of silently overwriting."""
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/a.py", "+y = 2")
    blocks = split_diff_by_file(diff)
    assert set(blocks) == {"src/cgis/a.py"}
    assert "+x = 1" in blocks["src/cgis/a.py"]
    assert "+y = 2" in blocks["src/cgis/a.py"]
