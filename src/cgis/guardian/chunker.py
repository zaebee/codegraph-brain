"""Connected-subgraph chunking of a PR diff (spec: 2026-06-11-guardian-chunker-design.md).

Slice 1 of #154: pure logic, no LLM calls; not wired into the review loop yet.
"""

import re

import structlog
from pydantic import BaseModel

from cgis.core.models import EdgeType
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore

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

    for test_file, impl_file in _test_pairs(files):
        union(test_file, impl_file)

    groups: dict[str, list[str]] = {}
    for f in files:  # files is sorted → each group list is sorted
        groups.setdefault(find(f), []).append(f)
    return [
        Chunk(files=tuple(group), diff="".join(blocks[f] for f in group))
        for group in sorted(groups.values())
    ]


_CHUNK_EDGE_TYPES = frozenset({EdgeType.IMPORTS, EdgeType.CALLS})


def _graph_pairs(
    store: SQLiteStore | None, changed: set[str], source_root: str
) -> list[tuple[str, str]]:
    """File pairs joined by an IMPORTS/CALLS edge with both endpoints changed.

    Graph paths are normalized with source_root (collector convention,
    fix 48790da). Any store failure degrades to no pairs — the chunker sits
    on the review path and must not take guardian down.
    """
    if store is None:
        return []
    prefix = f"{source_root}/" if source_root else ""
    try:
        fqn_to_file = {
            node.id: path
            for node in store.get_all_nodes()
            if (path := prefix + node.file_path) in changed
        }
        pairs: list[tuple[str, str]] = []
        for edge in store.get_all_edges():
            if edge.type not in _CHUNK_EDGE_TYPES or edge.target.startswith(RAW_CALL_PREFIX):
                continue
            src = fqn_to_file.get(edge.source)
            dst = fqn_to_file.get(edge.target)
            if src and dst and src != dst:
                pairs.append((src, dst))
    except Exception:
        log.warning("Graph connectivity skipped; falling back to isolated chunks.", exc_info=True)
        return []
    return pairs


_TEST_FILE_RE = re.compile(r"^tests/(?:.+/)?test_(?P<name>[^/]+)\.py$")


def _test_pairs(files: list[str]) -> list[tuple[str, str]]:
    """Pair each changed tests/**/test_X.py with its unique implementation file.

    Candidate = changed non-test .py whose path normalized to underscores
    (src/cgis/guardian/core.py → src_cgis_guardian_core) equals X or ends
    with "_X" — a bare suffix match would let test_index.py capture
    diff_index.py. Zero or several candidates → the test stays isolated.
    """
    impl = [f for f in files if f.endswith(".py") and not _TEST_FILE_RE.match(f)]
    norm = {f: f.removesuffix(".py").replace("/", "_") for f in impl}
    pairs: list[tuple[str, str]] = []
    for f in files:
        match = _TEST_FILE_RE.match(f)
        if not match:
            continue
        name = match.group("name")
        candidates = [i for i in impl if norm[i] == name or norm[i].endswith("_" + name)]
        if len(candidates) == 1:
            pairs.append((f, candidates[0]))
        elif candidates:
            log.debug("Ambiguous test pairing; left isolated.", test=f, candidates=candidates)
    return pairs
