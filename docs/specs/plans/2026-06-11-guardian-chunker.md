# Guardian Chunker Implementation Plan (Slice 1 of #154)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pure chunker module that turns a unified diff plus graph.db into connected-component review chunks — no LLM wiring.

**Architecture:** New `src/cgis/guardian/chunker.py` with `split_diff_by_file` (per-file diff blocks) and `build_chunks` (union-find over IMPORTS/CALLS edges between changed files + test-pairing heuristic). Spec: `docs/specs/2026-06-11-guardian-chunker-design.md`. Not imported by `core.py`/`runner.py` in this slice.

**Tech Stack:** Python 3.12, Pydantic frozen models, SQLiteStore reads, structlog, pytest. MyPy strict; interrogate ≥ 90%.

**Branch:** `feat/guardian-chunker` (spec already committed there as 68c93fa).

---

## File map

- Create: `src/cgis/guardian/chunker.py` — the whole feature.
- Create: `tests/unit/test_guardian_chunker.py` — all tests.
- No other file changes. Do NOT touch `core.py`, `runner.py`, `collector.py`.

Existing pieces you will reuse (do not reimplement):
- `SQLiteStore` (`src/cgis/storage/sqlite_store.py`): `connect()`, `save_graph(nodes, edges)`, `get_all_nodes()`, `get_all_edges()`, context-manager support, `RAW_CALL_PREFIX = "raw_call:"`.
- `Node`/`Edge`/`EdgeType`/`NodeType` (`src/cgis/core/models.py`): frozen Pydantic; `Node` requires `id, type, name, file_path, start_line, end_line`; `Edge` requires `id, source, target, type`.
- Graph paths vs diff paths: node `file_path` is relative to the ingest root (CI runs `cgis ingest ./src`), diff paths are repo-relative → normalize graph path as `f"{source_root}/{node.file_path}"` when `source_root` is non-empty (same convention as `ContextCollector`, fix 48790da).

---

### Task 1: `split_diff_by_file` — happy path

**Files:**
- Create: `src/cgis/guardian/chunker.py`
- Test: `tests/unit/test_guardian_chunker.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the guardian chunker (spec: 2026-06-11-guardian-chunker-design.md)."""

from cgis.guardian.chunker import split_diff_by_file


def fdiff(path: str, body: str = "+x = 1") -> str:
    """One minimal single-hunk diff block for `path`."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        f"{body}\n"
    )


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgis.guardian.chunker'`

- [ ] **Step 3: Write the implementation**

```python
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
    return new or old


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/chunker.py tests/unit/test_guardian_chunker.py
git commit -m "feat(guardian): split_diff_by_file — per-file diff blocks"
```

---

### Task 2: `split_diff_by_file` — edge cases

**Files:**
- Modify: `src/cgis/guardian/chunker.py` (only `_block_path`)
- Test: `tests/unit/test_guardian_chunker.py`

- [ ] **Step 1: Write the failing tests** (append to the test file)

```python
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
    diff = (
        "diff --git a/logo.png b/logo.png\n"
        "Binary files a/logo.png and b/logo.png differ\n"
    )
    assert set(split_diff_by_file(diff)) == {"logo.png"}


def test_split_unparsable_block_skipped() -> None:
    """A block with no parsable path is skipped (logged), never raised on (spec §5)."""
    assert split_diff_by_file("diff --git\ngarbage\n") == {}
```

- [ ] **Step 2: Run tests to verify the binary one fails**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: `test_split_binary_block_keyed_via_git_header` FAILS (block skipped, dict empty); the other four PASS already — keep them, they pin the behavior.

- [ ] **Step 3: Add the `diff --git` fallback to `_block_path`**

Replace the final `return new or old` of `_block_path` with:

```python
    return new or old or _git_header_path(lines[0])
```

and add below `_block_path`:

```python
def _git_header_path(header: str) -> str | None:
    """Fallback for blocks without ---/+++ headers (binary, mode-only): b/ side."""
    marker = " b/"
    idx = header.rfind(marker)
    if idx == -1:
        return None
    return header[idx + len(marker) :] or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/chunker.py tests/unit/test_guardian_chunker.py
git commit -m "feat(guardian): chunker handles deletions, renames, binary blocks"
```

---

### Task 3: `Chunk` model + `build_chunks` without a graph

**Files:**
- Modify: `src/cgis/guardian/chunker.py`
- Test: `tests/unit/test_guardian_chunker.py`

- [ ] **Step 1: Write the failing tests** (append; extend the imports at the top of the test file)

```python
import pytest
from pydantic import ValidationError

from cgis.guardian.chunker import Chunk, build_chunks, split_diff_by_file


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: FAIL with `ImportError: cannot import name 'Chunk'`

- [ ] **Step 3: Implement `Chunk` and the degenerate `build_chunks`**

Add to `chunker.py` (imports at the top of the file):

```python
from pydantic import BaseModel

from cgis.storage.sqlite_store import SQLiteStore
```

and below `split_diff_by_file`:

```python
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
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
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
    store: SQLiteStore | None, changed: set[str], source_root: str
) -> list[tuple[str, str]]:
    """File pairs joined by an IMPORTS/CALLS edge with both endpoints changed."""
    return []  # graph connectivity lands in the next task
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: 10 PASS

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/chunker.py tests/unit/test_guardian_chunker.py
git commit -m "feat(guardian): Chunk model + build_chunks degenerate path"
```

---

### Task 4: graph connectivity (`_graph_pairs`)

**Files:**
- Modify: `src/cgis/guardian/chunker.py` (replace the `_graph_pairs` stub)
- Test: `tests/unit/test_guardian_chunker.py`

- [ ] **Step 1: Write the failing tests** (append; add imports + helpers to the test file)

```python
from pathlib import Path

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.storage.sqlite_store import SQLiteStore


def _node(fqn: str, file_path: str) -> Node:
    """Minimal MODULE node for graph fixtures."""
    return Node(
        id=fqn,
        type=NodeType.MODULE,
        name=fqn.rsplit(".", 1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=1,
    )


def _edge(source: str, target: str, etype: EdgeType = EdgeType.IMPORTS) -> Edge:
    """Minimal edge for graph fixtures."""
    return Edge(id=f"{source}->{target}:{etype}", source=source, target=target, type=etype)


def _make_store(tmp_path: Path, nodes: list[Node], edges: list[Edge]) -> SQLiteStore:
    """Persist a small synthetic graph and return the connected store."""
    store = SQLiteStore(str(tmp_path / "graph.db"))
    store.connect()
    store.save_graph(nodes, edges)
    return store


def test_imports_edge_joins_two_files(tmp_path: Path) -> None:
    """a.py imports b.py, both changed → one two-file chunk."""
    store = _make_store(
        tmp_path,
        [_node("src.cgis.a", "src/cgis/a.py"), _node("src.cgis.b", "src/cgis/b.py")],
        [_edge("src.cgis.a", "src.cgis.b")],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    chunks = build_chunks(diff, store)
    assert [c.files for c in chunks] == [("src/cgis/a.py", "src/cgis/b.py")]
    assert "+x = 1" in chunks[0].diff


def test_calls_edge_joins_symbol_level_nodes(tmp_path: Path) -> None:
    """CALLS between function nodes connects their FILES via node.file_path."""
    store = _make_store(
        tmp_path,
        [_node("src.cgis.a.run", "src/cgis/a.py"), _node("src.cgis.b.go", "src/cgis/b.py")],
        [_edge("src.cgis.a.run", "src.cgis.b.go", EdgeType.CALLS)],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    assert [c.files for c in build_chunks(diff, store)] == [("src/cgis/a.py", "src/cgis/b.py")]


def test_indirect_path_through_unchanged_file_does_not_join(tmp_path: Path) -> None:
    """A→X→B with only A and B changed → A and B stay separate (induced subgraph)."""
    store = _make_store(
        tmp_path,
        [
            _node("src.cgis.a", "src/cgis/a.py"),
            _node("src.cgis.x", "src/cgis/x.py"),
            _node("src.cgis.b", "src/cgis/b.py"),
        ],
        [_edge("src.cgis.a", "src.cgis.x"), _edge("src.cgis.x", "src.cgis.b")],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")  # x.py NOT in the diff
    assert [c.files for c in build_chunks(diff, store)] == [
        ("src/cgis/a.py",),
        ("src/cgis/b.py",),
    ]


def test_non_chunk_edge_types_ignored(tmp_path: Path) -> None:
    """CONTAINS/DECLARES etc. carry structure, not coupling — they must not join."""
    store = _make_store(
        tmp_path,
        [_node("src.cgis.a", "src/cgis/a.py"), _node("src.cgis.b", "src/cgis/b.py")],
        [_edge("src.cgis.a", "src.cgis.b", EdgeType.CONTAINS)],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    assert len(build_chunks(diff, store)) == 2


def test_source_root_normalization(tmp_path: Path) -> None:
    """Graph paths are ingest-root-relative (cgis/...), diff paths repo-relative (src/cgis/...)."""
    store = _make_store(
        tmp_path,
        [_node("cgis.a", "cgis/a.py"), _node("cgis.b", "cgis/b.py")],
        [_edge("cgis.a", "cgis.b")],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    chunks = build_chunks(diff, store, source_root="src")
    assert [c.files for c in chunks] == [("src/cgis/a.py", "src/cgis/b.py")]


def test_raw_call_target_skipped(tmp_path: Path) -> None:
    """Unresolved raw_call: targets have no node and must not crash or join."""
    store = _make_store(
        tmp_path,
        [_node("src.cgis.a", "src/cgis/a.py"), _node("src.cgis.b", "src/cgis/b.py")],
        [_edge("src.cgis.a", "raw_call:mystery", EdgeType.CALLS)],
    )
    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    assert len(build_chunks(diff, store)) == 2
```

- [ ] **Step 2: Run tests to verify the joining ones fail**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: `test_imports_edge_joins_two_files`, `test_calls_edge_joins_symbol_level_nodes`, `test_source_root_normalization` FAIL (stub returns no pairs → files isolated); the negative tests PASS.

- [ ] **Step 3: Implement `_graph_pairs`**

Replace the stub. Also add `RAW_CALL_PREFIX` and `EdgeType` to the imports:

```python
from cgis.core.models import EdgeType
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore
```

```python
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
    except Exception:  # noqa: BLE001 — same posture as collect_drift: degrade, log, never raise
        log.warning("Graph connectivity skipped; falling back to isolated chunks.", exc_info=True)
        return []
    return pairs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: 16 PASS

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/chunker.py tests/unit/test_guardian_chunker.py
git commit -m "feat(guardian): connected components over IMPORTS/CALLS edges"
```

---

### Task 5: test-pairing heuristic

**Files:**
- Modify: `src/cgis/guardian/chunker.py`
- Test: `tests/unit/test_guardian_chunker.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_test_file_attaches_to_its_module(tmp_path: Path) -> None:
    """tests/unit/test_guardian_core.py joins the chunk of src/cgis/guardian/core.py."""
    store = _make_store(tmp_path, [_node("src.cgis.guardian.core", "src/cgis/guardian/core.py")], [])
    diff = fdiff("src/cgis/guardian/core.py") + fdiff("tests/unit/test_guardian_core.py")
    chunks = build_chunks(diff, store)
    assert [c.files for c in chunks] == [
        ("src/cgis/guardian/core.py", "tests/unit/test_guardian_core.py")
    ]


def test_test_pairing_needs_underscore_boundary() -> None:
    """'core' must not match score.py — suffix only counts at an underscore boundary."""
    diff = fdiff("src/cgis/score.py") + fdiff("tests/unit/test_core.py")
    chunks = build_chunks(diff, store=None)
    assert [c.files for c in chunks] == [
        ("src/cgis/score.py",),
        ("tests/unit/test_core.py",),
    ]


def test_ambiguous_test_pairing_stays_isolated() -> None:
    """test_engine.py with two engine.py candidates changed → isolated."""
    diff = (
        fdiff("src/cgis/resolver/engine.py")
        + fdiff("src/cgis/query/engine.py")
        + fdiff("tests/unit/test_engine.py")
    )
    chunks = build_chunks(diff, store=None)
    assert ("tests/unit/test_engine.py",) in [c.files for c in chunks]


def test_non_tests_dir_file_not_paired() -> None:
    """Only files under tests/ participate in pairing (spec §4.2.5)."""
    diff = fdiff("scripts/test_helper.py") + fdiff("src/cgis/helper.py")
    chunks = build_chunks(diff, store=None)
    assert len(chunks) == 2
```

- [ ] **Step 2: Run tests to verify the attach one fails**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: `test_test_file_attaches_to_its_module` FAILS (test file isolated); the negative ones PASS.

- [ ] **Step 3: Implement `_test_pairs` and wire it into `build_chunks`**

Add to `chunker.py`:

```python
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
```

In `build_chunks`, after the `_graph_pairs` loop, add:

```python
    for test_file, impl_file in _test_pairs(files):
        union(test_file, impl_file)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: 20 PASS

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/chunker.py tests/unit/test_guardian_chunker.py
git commit -m "feat(guardian): pair changed test files with their module's chunk"
```

---

### Task 6: store-failure degradation + determinism

**Files:**
- Test only: `tests/unit/test_guardian_chunker.py` (the production code from Task 4 already degrades; these tests pin it)

- [ ] **Step 1: Write the tests** (append)

```python
def test_broken_store_degrades_to_isolated_chunks() -> None:
    """A store that raises on read → isolated chunks, no exception escapes."""

    class _BrokenStore(SQLiteStore):
        def get_all_nodes(self) -> list[Node]:
            raise RuntimeError("corrupt db")

    diff = fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    chunks = build_chunks(diff, _BrokenStore(":memory:"))
    assert [c.files for c in chunks] == [("src/cgis/a.py",), ("src/cgis/b.py",)]


def test_build_chunks_deterministic(tmp_path: Path) -> None:
    """Same inputs twice → byte-identical output."""
    store = _make_store(
        tmp_path,
        [_node("src.cgis.a", "src/cgis/a.py"), _node("src.cgis.b", "src/cgis/b.py")],
        [_edge("src.cgis.a", "src.cgis.b")],
    )
    diff = fdiff("src/cgis/c.py") + fdiff("src/cgis/a.py") + fdiff("src/cgis/b.py")
    assert build_chunks(diff, store) == build_chunks(diff, store)
```

- [ ] **Step 2: Run tests — both should pass immediately**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: 22 PASS. If `test_broken_store_degrades_to_isolated_chunks` raises instead, the `except Exception` in `_graph_pairs` got lost — restore it.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_guardian_chunker.py
git commit -m "test(guardian): pin chunker degradation and determinism"
```

---

### Task 7: full verification

**Files:** none new.

- [ ] **Step 1: Run the full gate suite**

Run: `make format && make lint && make type-check && make pytest && make doc-coverage`
Expected: all green — ruff clean, `Success: no issues found` (mypy strict), all tests pass (631 pre-existing + 22 new), interrogate ≥ 90%.

- [ ] **Step 2: Fix anything the gates flag, amend or commit as needed**

Typical traps: missing docstrings on test helpers (interrogate counts tests), walrus-in-comprehension typing, `BLE001` needs its inline justification comment kept.

- [ ] **Step 3: Final commit (if there were fixes)**

```bash
git add -A src tests
git commit -m "chore(guardian): chunker gate fixes"
```

---

## Out of plan (controller handles after Task 7)

Push branch (`git push origin feat/guardian-chunker` — bare `git push` hits GH013), open the PR referencing #154 ("slice 1"), and run the live guardian inline-path test on it. NEVER stage `.claude/`, `.superpowers/`, `.env`.
