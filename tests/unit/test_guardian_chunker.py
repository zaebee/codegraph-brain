"""Unit tests for the guardian chunker (spec: 2026-06-11-guardian-chunker-design.md)."""

import pytest
from pydantic import ValidationError

from cgis.guardian.chunker import Chunk, build_chunks, split_diff_by_file


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
