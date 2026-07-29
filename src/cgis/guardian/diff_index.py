"""Pure unified-diff parsers: RIGHT-side line index, and the per-file split.

Two families of `---`/`+++` header patterns live here on purpose, and they are
NOT interchangeable. The line indexer keys on the new path only and ignores
git's C-quoting; the per-file splitter is quote-aware and falls back to the old
path so deletions stay reviewable. Keeping them side by side makes the
difference visible instead of accidental.
"""

import re

import structlog

log = structlog.getLogger(__name__)

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")

# Quote-aware variants used by split_diff_by_file. Git C-quotes paths with
# special characters: `--- "a/x y.py"` — the optional quotes wrap the WHOLE
# `a/...` token, so they must be stripped around the prefix (PR #157 review).
_QUOTED_OLD_FILE_RE = re.compile(r'^--- "?(?:a/)?(.+?)"?$')
_QUOTED_NEW_FILE_RE = re.compile(r'^\+\+\+ "?(?:b/)?(.+?)"?$')


def _new_file_path(header: re.Match[str]) -> str | None:
    """New-side path from a matched `+++` header; None for deletions (/dev/null)."""
    path = header.group(1)
    return None if path == "/dev/null" else path


def diff_line_content(diff_text: str) -> dict[str, dict[int, str]]:
    """Map each changed file (new path) to ``{RIGHT-side line number: line text}``.

    The text is the line content with its leading diff marker (``+``/`` ``)
    stripped, so it can be matched verbatim against a finding's quote to anchor
    the comment deterministically (#181). Same right-side accounting as
    :func:`diff_line_index`: added and context lines carry a number, removed
    lines don't, and ``+++ /dev/null`` deletions are excluded.
    """
    content: dict[str, dict[int, str]] = {}
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
            content.setdefault(current, {})
            continue
        if not in_hunk or current is None or line.startswith(("-", "\\")):
            continue  # outside a hunk / removed line / "\ No newline" marker
        content[current][new_line] = line[1:]  # drop the '+'/' ' marker, keep the text
        new_line += 1
    return {path: lines for path, lines in content.items() if lines}


def diff_line_index(diff_text: str) -> dict[str, set[int]]:
    """Map each changed file (new path) to the set of RIGHT-side line numbers.

    GitHub only accepts inline review comments on lines present in the diff;
    context and added lines count, removed lines do not (spec §6.2). Renames
    are keyed by the new path so keys match Finding.file. Files deleted in the
    PR (+++ /dev/null) have no RIGHT side and are excluded. Derived from
    :func:`diff_line_content` so the two never diverge.
    """
    return {path: set(lines) for path, lines in diff_line_content(diff_text).items()}


def _block_path(lines: list[str]) -> str | None:
    """Path for one diff block: the new path, or the old path for deletions.

    Headers are only read before the first `@@` line — past that, a
    `+++ ...` line is added content (the 5e53dd0 lesson, avoided structurally).
    """
    old: str | None = None
    new: str | None = None
    for line in lines:
        if line.startswith("@@"):
            break
        if old is None and (m := _QUOTED_OLD_FILE_RE.match(line)):
            old = None if m.group(1) == "/dev/null" else m.group(1)
        elif new is None and (m := _QUOTED_NEW_FILE_RE.match(line)):
            new = None if m.group(1) == "/dev/null" else m.group(1)
    return new or old or _git_header_path(lines[0])


def _git_header_path(header: str) -> str | None:
    """Fallback for blocks without ---/+++ headers (binary, mode-only): b/ side.

    Tries the quoted form first (`diff --git "a/x y.png" "b/x y.png"` — the
    quote sits before b/, so a bare ` b/` search misses it; gemini review,
    PR #157), then the plain ` b/` marker.
    """
    quoted = ' "b/'
    idx = header.rfind(quoted)
    if idx != -1:
        return header[idx + len(quoted) :].removesuffix('"') or None
    marker = " b/"
    idx = header.rfind(marker)
    if idx == -1:
        return None
    return header[idx + len(marker) :] or None


def split_diff_by_file(diff_text: str) -> dict[str, str]:
    """Split a unified diff into per-file blocks keyed by repo-relative path.

    Key = new path; deletions (`+++ /dev/null`) fall back to the old path so
    deleted files stay reviewable (unlike diff_line_index, which drops them — no
    RIGHT side to anchor an inline comment on). Splitting on a column-zero
    `diff --git` is safe: inside a hunk every content line carries a
    `+`/`-`/space prefix, so a bare header can only be a real one.
    """
    blocks: dict[str, str] = {}
    current: list[str] = []

    def _flush() -> None:
        if not current:
            return
        path = _block_path(current)
        if path is None:
            log.warning("Diff block without parsable path skipped.", head=current[0])
        else:
            block = "\n".join(current) + "\n"
            if path in blocks:
                log.warning("Duplicate diff block for path; merging.", path=path)
                blocks[path] += block
            else:
                blocks[path] = block
        current.clear()

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            _flush()
        current.append(line)
    _flush()
    return blocks
