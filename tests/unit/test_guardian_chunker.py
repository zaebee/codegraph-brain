"""Unit tests for the guardian chunker (spec: 2026-06-11-guardian-chunker-design.md)."""

from cgis.guardian.chunker import split_diff_by_file


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
