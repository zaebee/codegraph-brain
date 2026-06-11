"""Connected-subgraph chunking of a PR diff (spec: 2026-06-11-guardian-chunker-design.md).

Slice 1 of #154: pure logic, no LLM calls; not wired into the review loop yet.
"""

import re

import structlog

log = structlog.getLogger(__name__)

_OLD_FILE_RE = re.compile(r"^--- (?:a/)?(.+)$")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")


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
        if old is None and (m := _OLD_FILE_RE.match(line)):
            old = None if m.group(1) == "/dev/null" else m.group(1)
        elif new is None and (m := _NEW_FILE_RE.match(line)):
            new = None if m.group(1) == "/dev/null" else m.group(1)
    return new or old or _git_header_path(lines[0])


def _git_header_path(header: str) -> str | None:
    """Fallback for blocks without ---/+++ headers (binary, mode-only): b/ side."""
    marker = " b/"
    idx = header.rfind(marker)
    if idx == -1:
        return None
    return header[idx + len(marker) :] or None


def split_diff_by_file(diff_text: str) -> dict[str, str]:
    """Split a unified diff into per-file blocks keyed by repo-relative path.

    Key = new path; deletions (`+++ /dev/null`) fall back to the old path so
    deleted files stay reviewable (unlike diff_index, which drops them — no
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
            blocks[path] = "\n".join(current) + "\n"
        current.clear()

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            _flush()
        current.append(line)
    _flush()
    return blocks
