"""Pure unified-diff parser: which RIGHT-side lines can carry an inline comment."""

import re

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")


def _new_file_path(header: re.Match[str]) -> str | None:
    """New-side path from a matched `+++` header; None for deletions (/dev/null)."""
    path = header.group(1)
    return None if path == "/dev/null" else path


def diff_line_index(diff_text: str) -> dict[str, set[int]]:
    """Map each changed file (new path) to the set of RIGHT-side line numbers.

    GitHub only accepts inline review comments on lines present in the diff;
    context and added lines count, removed lines do not (spec §6.2). Renames
    are keyed by the new path so keys match Finding.file. Files deleted in the
    PR (+++ /dev/null) have no RIGHT side and are excluded.
    """
    index: dict[str, set[int]] = {}
    current: str | None = None
    in_hunk = False
    new_line = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            current = None
            in_hunk = False
            continue
        # Real `+++` headers only appear between hunks (after `diff --git`
        # resets in_hunk); inside a hunk a `+++ ...` line is added CONTENT
        # whose text starts with `++` — counting it as a header would both
        # drop the rest of the file and shift line numbers.
        if not in_hunk and (header := _NEW_FILE_RE.match(line)):
            current = _new_file_path(header)
            continue
        if (hunk := _HUNK_RE.match(line)) and current is not None:
            new_line = int(hunk.group(1))
            in_hunk = True
            index.setdefault(current, set())
            continue
        if not in_hunk or current is None or line.startswith(("-", "\\")):
            continue  # outside a hunk / removed line / "\ No newline" marker
        index[current].add(new_line)  # added or context line: has a RIGHT-side number
        new_line += 1
    return {path: lines for path, lines in index.items() if lines}
