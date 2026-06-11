"""Connected-subgraph chunking of a PR diff (spec: 2026-06-11-guardian-chunker-design.md).

Slice 1 of #154: pure logic, no LLM calls; not wired into the review loop yet.
"""

import re

import structlog
from pydantic import BaseModel

from cgis.storage.sqlite_store import SQLiteStore

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


class Chunk(BaseModel, frozen=True):
    """One connected group of changed files and its slice of the diff."""

    files: tuple[str, ...]
    diff: str


def build_chunks(
    diff_text: str,
    store: SQLiteStore | None,
    source_root: str = "",
) -> list[Chunk]:
    """Group changed files into connected-component chunks via IMPORTS/CALLS.

    Degrades honestly: no store / file absent from the graph / store errors →
    isolated single-file chunks, never worse than the unchunked status quo.
    Deterministic: files sorted inside a chunk, chunks sorted by first file.
    """
    blocks = split_diff_by_file(diff_text)
    if not blocks:
        return []
    files = sorted(blocks)
    parent: dict[str, str] = {f: f for f in files}

    def find(x: str) -> str:
        """Find root of union-find tree with path halving."""
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        """Merge two sets in the union-find structure."""
        parent[find(a)] = find(b)

    for a, b in _graph_pairs(store, set(files), source_root):
        union(a, b)

    groups: dict[str, list[str]] = {}
    for f in files:  # files is sorted → each group list is sorted
        groups.setdefault(find(f), []).append(f)
    return [
        Chunk(files=tuple(group), diff="".join(blocks[f] for f in group))
        for group in sorted(groups.values())
    ]


def _graph_pairs(
    store: SQLiteStore | None,  # noqa: ARG001
    changed: set[str],  # noqa: ARG001
    source_root: str,  # noqa: ARG001
) -> list[tuple[str, str]]:
    """File pairs joined by an IMPORTS/CALLS edge with both endpoints changed."""
    return []  # graph connectivity lands in the next task
