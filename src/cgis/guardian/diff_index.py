"""Pure unified-diff parser: which RIGHT-side lines can carry an inline comment."""

import re

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")


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
        file_match = _NEW_FILE_RE.match(line)
        if file_match:
            path = file_match.group(1)
            current = None if path == "/dev/null" else path
            in_hunk = False
            continue
        hunk_match = _HUNK_RE.match(line)
        if hunk_match and current is not None:
            new_line = int(hunk_match.group(1))
            in_hunk = True
            index.setdefault(current, set())
            continue
        if current is None or not in_hunk:
            continue
        if line.startswith("+"):
            index[current].add(new_line)
            new_line += 1
        elif line.startswith(("-", "\\")):
            continue  # removed line / "\ No newline" marker: no RIGHT-side line
        else:
            index[current].add(new_line)  # context line
            new_line += 1
    return {path: lines for path, lines in index.items() if lines}
