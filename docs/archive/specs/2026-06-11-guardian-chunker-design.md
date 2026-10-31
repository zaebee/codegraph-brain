# Guardian Chunker — Connected-Subgraph Chunking (Slice 1 of #154)

**Status:** approved design, slice 1 (pure chunker, no LLM wiring)
**Issue:** #154 — guardian: chunked review over connected subgraphs (large-PR LGTM fix)
**Date:** 2026-06-11

## 1. Problem

Plan 2 ablation (Task 12) measured that large diffs (60K+ prompt tokens) make the
finder collapse to LGTM, and that adding context does not help: `full_files`
pushed PR 144 to 94K tokens while recall stayed 0.0 on all three large PRs
(142/143/144). The failure mode is attention dilution, not missing context.

The fix proposed in #154: split the review into connected subgraphs of the
change — graph.db already knows the connectivity. Each subgraph ("chunk") gets
its own focused finder pass; findings are deduped, skeptic-checked once, and
rendered as one report.

## 2. Scope of this slice

This spec covers **only the chunker**: the pure function that turns a unified
diff plus a code graph into an ordered list of chunks. It ships as a new module
with unit tests and is **not wired into `core.py` / `runner.py`** — no LLM
calls, no `GUARDIAN_FEATURES` flag, no benchmark run.

Out of scope (slice 2+, each benchmark-gated against the committed
gemini-3.5-flash baseline — mean recall 0.19, noise 0.17/PR):

- Per-chunk finder passes, finding dedup, single skeptic pass over the union.
- The quotient-level "architecture chunk" for cross-chunk contract breaks.
- Forced splitting of oversized components (e.g. greedy min-cut by
  `Edge.weight`). Slice 1 keeps pure components; add splitting only if the
  bench shows components congeal.
- One-hop connectivity through unchanged intermediary files (A→X→B with only
  A, B changed). Slice 1 uses the induced subgraph: direct edges between
  changed files only.

## 3. Contract

New module: `src/cgis/guardian/chunker.py`. Pure logic; the only I/O is reads
through an already-open `SQLiteStore`.

```python
class Chunk(BaseModel, frozen=True):
    """One connected group of changed files and its slice of the diff."""
    files: tuple[str, ...]   # repo-relative paths, sorted
    diff: str                # concatenation of the per-file diff blocks


def split_diff_by_file(diff_text: str) -> dict[str, str]:
    """Split a unified diff into per-file blocks, keyed by repo-relative path."""


def build_chunks(
    diff_text: str,
    store: SQLiteStore | None,
    source_root: str = "",
) -> list[Chunk]:
    """Group changed files into connected-component chunks via IMPORTS/CALLS."""
```

Degradation is honest: `store=None` (or a file absent from the graph) yields
isolated single-file chunks — never worse than the status quo, just without
grouping.

## 4. Algorithm

### 4.1 `split_diff_by_file`

1. Split on lines that `startswith("diff --git")` **at column zero**. This is
   safe by construction: inside a hunk every content line carries a prefix
   (`+`, `-`, or space), so a bare `diff --git` at column zero can only be a
   real file header. (Same class of trap as the `+++`-inside-hunk bug fixed in
   5e53dd0 — here it is avoided structurally; our own test fixtures that embed
   diff text inside a diff stay parseable.)
2. Within each block, read the `--- `/`+++ ` headers **only before the first
   `@@` hunk line** (after that, `+++ ...` is added content).
3. Key selection:
   - normal change / rename: the **new** path (`+++ b/<path>`) — consistent
     with `Finding.file` and `diff_index.py`;
   - deletion (`+++ /dev/null`): the **old** path (`--- a/<path>`). Unlike
     `diff_index` (which drops deletions — no RIGHT side to anchor an inline
     comment), the chunker keeps them: deletions get reviewed too;
   - binary or header-only blocks: keep the block under its path with whatever
     text exists. Never raise.

### 4.2 `build_chunks`

1. `split_diff_by_file` → changed paths and their diff slices. Empty diff →
   `[]`.
2. From the store: every node's `fqn → file_path`, normalized as
   `source_root + "/" + node.file_path` when `source_root` is non-empty —
   the same convention as the collector fix 48790da (graph paths are relative
   to the ingest root; diff paths are repo-relative).
3. Collect IMPORTS and CALLS edges where **both** endpoints resolve to changed
   files. Skip edges with a `raw_call:` target or an endpoint without a
   `file_path`. Each such edge connects two changed files.
4. Union-find over changed files → connected components. Files outside the
   graph, without qualifying edges, or with `store=None` stay isolated.
5. **Test-pairing heuristic** (changed files matching `tests/**/test_*.py`,
   which are never in graph.db — CI ingests `src/` only):
   - strip the `test_` prefix and `.py` suffix → `<name>`;
   - a candidate is a changed non-test `.py` file whose path, normalized by
     joining directories with `_` and dropping `.py`
     (`src/cgis/guardian/core.py` → `src_cgis_guardian_core`), **ends with
     `<name>` at an underscore boundary** (normalized path `== <name>` or
     ends with `"_" + <name>`) — a bare suffix match would let
     `test_index.py` capture `diff_index.py`. The boundary rule still admits
     rare false positives (a hypothetical `test_index.py` alongside a changed
     `diff_index.py` matches `_index`); accepted — worst case is a slightly
     fatter chunk, and the 0-or-many guard below catches real collisions;
   - exactly one candidate → the test joins that candidate's chunk; zero or
     several candidates (e.g. `test_engine.py` with both `resolver/engine.py`
     and `query/engine.py` changed) → the test stays isolated.
   - This covers `test_core.py`, `test_guardian_core.py`, and
     `test_python_extractor.py` → `extractors/python_extractor.py`; a naive
     stem match would not.
6. Determinism: files within a chunk sorted; chunks sorted by their first
   file; a chunk's `diff` concatenates blocks in file-sorted order.

## 5. Error handling

- Malformed diff blocks (no parsable path) are skipped with a structlog
  warning, never raised — the chunker sits on the review path and must not
  take guardian down.
- A store that raises on read (corrupt/locked db) is treated as `store=None`
  for the rest of the call: log a warning, fall back to isolated chunks.

## 6. Testing

`tests/unit/test_guardian_chunker.py`, pure unit tests:

- **`split_diff_by_file`**: multi-file diff; rename; deletion keyed by old
  path; binary block; diff-text-embedded-in-a-diff (added lines containing
  `+diff --git ...`).
- **`build_chunks` with a real `SQLiteStore` in `tmp_path`** (existing test
  pattern) on small synthetic graphs: two files joined by one IMPORTS edge;
  CALLS edge; three files where only direct edges count (A–X–B with X
  unchanged → A, B separate); `source_root` normalization; `store=None`.
- **Test-pairing**: attach on unique candidate; isolation on zero and on
  ambiguous candidates; multiple tests attaching to one chunk.
- **Determinism**: same inputs twice → identical output order.

Quality gates as everywhere: `make format && make lint && make type-check &&
make pytest && make doc-coverage` (mypy strict, interrogate ≥ 90%).

## 7. Roadmap fit

Slice 2 wires chunks into the finder loop behind `GUARDIAN_FEATURES=chunked`
(per-chunk prompts, dedup, one skeptic pass) and runs the benchmark gate.
Slice 3 adds the quotient "architecture chunk". Forced splitting and one-hop
expansion stay parked until the bench demands them.
