"""Unit tests for the pure unified-diff RIGHT-side line indexer (spec §6.2)."""

from cgis.guardian.diff_index import diff_line_index

_SIMPLE = """\
diff --git a/src/x.py b/src/x.py
index 111..222 100644
--- a/src/x.py
+++ b/src/x.py
@@ -10,4 +10,5 @@ def f():
 context1
-removed
+added1
+added2
 context2
"""

_RENAME = """\
diff --git a/old.py b/new.py
similarity index 90%
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1,2 +1,2 @@
 keep
+fresh
"""

_NEW_FILE = """\
diff --git a/brand.py b/brand.py
new file mode 100644
--- /dev/null
+++ b/brand.py
@@ -0,0 +1,2 @@
+line1
+line2
"""

_DELETED = """\
diff --git a/dead.py b/dead.py
deleted file mode 100644
--- a/dead.py
+++ /dev/null
@@ -1,2 +0,0 @@
-line1
-line2
"""


def test_added_and_context_lines_indexed() -> None:
    """RIGHT side = context + added lines, numbered from the hunk's +start."""
    index = diff_line_index(_SIMPLE)
    # @@ +10,5: 10=context1, 11=added1, 12=added2, 13=context2 (removed has no RIGHT line)
    assert index["src/x.py"] == {10, 11, 12, 13}


def test_rename_keyed_by_new_path() -> None:
    """Renames are keyed by the NEW path so keys match Finding.file."""
    index = diff_line_index(_RENAME)
    assert "new.py" in index
    assert "old.py" not in index
    assert index["new.py"] == {1, 2}


def test_new_file_all_lines() -> None:
    """A new file's lines are all commentable."""
    assert diff_line_index(_NEW_FILE)["brand.py"] == {1, 2}


def test_deleted_file_absent() -> None:
    """A deleted file has no RIGHT side — not in the index at all."""
    assert diff_line_index(_DELETED) == {}


def test_empty_diff() -> None:
    """Empty input → empty index, no crash."""
    assert diff_line_index("") == {}


def test_multiple_files_and_hunks() -> None:
    """Two files in one diff each get their own line set."""
    index = diff_line_index(_SIMPLE + _NEW_FILE)
    assert set(index) == {"src/x.py", "brand.py"}
