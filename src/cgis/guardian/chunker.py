"""Connected-subgraph chunking of a PR diff (spec: 2026-06-11-guardian-chunker-design.md).

Slice 1 of #154: pure logic, no LLM calls; not wired into the review loop yet.
"""

import re

import structlog
from pydantic import BaseModel

from cgis.core.models import EdgeType

# Re-exported: the per-file split moved to the pure diff leaf next to the other
# unified-diff parsers, since the skeptic pass needs it too (#246 plan T3).
from cgis.guardian.diff_index import split_diff_by_file as split_diff_by_file  # noqa: PLC0414
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore

log = structlog.getLogger(__name__)


class Chunk(BaseModel, frozen=True):
    """One connected group of changed files and its slice of the diff."""

    files: tuple[str, ...]
    diff: str


def build_chunks(
    diff_text: str,
    store: SQLiteStore | None,
    source_root: str = "",
    reviewable_suffixes: tuple[str, ...] = (".py",),
) -> list[Chunk]:
    """Group changed files into connected-component chunks via IMPORTS/CALLS.

    Only files ending in ``reviewable_suffixes`` are chunked. A markdown or lock
    file would otherwise cost one finder call and receive no context at all —
    ``collect_for_chunk`` narrows to .py before assembling one (#277). An
    all-filtered diff returns [], which the caller distinguishes from an empty
    diff and answers with a single-pass fallback.

    Degrades honestly: no store / file absent from the graph / store errors →
    isolated single-file chunks, never worse than the unchunked status quo.
    Deterministic: files sorted inside a chunk, chunks sorted by first file.
    """
    blocks = {
        path: block
        for path, block in split_diff_by_file(diff_text).items()
        if path.endswith(reviewable_suffixes)
    }
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
        # Load only the changed files' nodes and their outgoing edges instead
        # of the whole graph: any qualifying edge has BOTH endpoints changed,
        # so its source is always among these nodes (gemini review, PR #157).
        fqn_to_file = {
            node.id: prefix + node.file_path
            for path in changed
            if not prefix or path.startswith(prefix)
            for node in store.get_nodes_by_file(path.removeprefix(prefix))
        }
        pairs: list[tuple[str, str]] = []
        for edge in store.get_outgoing_edges_batch(list(fqn_to_file)):
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
    (src/cgis/guardian/core.py -> src_cgis_guardian_core) equals X or ends
    with "_X". The underscore boundary stops cross-word bleed (test_core.py
    must not capture score.py); same-suffix module names can still collide
    (test_index.py would match diff_index.py) — accepted per spec §4.2.5,
    and the unique-candidate rule below limits the blast radius. Zero or
    several candidates -> the test stays isolated.
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
