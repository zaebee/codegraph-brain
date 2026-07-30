# Chunked Review Source Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the chunked review path from spending an API call per markdown file, so the recall bench #277 asks for measures a configuration someone would actually enable.

**Architecture:** `build_chunks` gains a `reviewable_suffixes=(".py",)` parameter and drops non-matching diff blocks before union-find grouping. Because filtering can empty the chunk list for a docs-only PR — which single-pass reviews today — `run_chunked_review` falls back to the unchunked reviewer in that case, via a helper shared with the routing default so the two cannot drift.

**Tech Stack:** Python 3.12+, pytest (`asyncio_mode = auto`, though `test_guardian_chunked.py` also marks tests explicitly — match the file you are editing).

**Spec:** `docs/specs/2026-07-30-chunk-source-filter-design.md`
**Issue:** #277

## Global Constraints

- **MyPy strict** (`make type-check` runs `mypy src`). Full annotations including return types.
- **Ruff** full rule set, line length **100**, double quotes. `SLF001` is on with no per-file test ignore — use inline `# noqa: SLF001  # <reason>`, as `test_guardian_chunked.py:124` already does for `_diff_cache`.
- **Docstring coverage ≥ 90%** (`uv run interrogate src`).
- **Routing stays flag-based.** Do not touch `chunked = "chunked" in collector.features`. Enabling chunking is gated on recall evidence and is not part of this work.
- **No prompt, skeptic or scoring change.**
- **Verify in a CI-shaped environment**: CI runs `uv sync --group dev` (no guardian group), and `uv run mypy src` must pass there. This divergence is what turned #278's CI red.
- **Full verification before every commit:** `make format && make lint && make type-check && make pytest && make doc-coverage`.
- Branch `feat/277-chunk-routing`, worktree `.claude/worktrees/chunk-routing`.

## File Structure

| File | Responsibility |
|------|----------------|
| `src/cgis/guardian/chunked.py` (modify) | `_single_pass` helper; the no-reviewable-files fallback |
| `src/cgis/guardian/chunker.py` (modify) | `reviewable_suffixes` parameter and the filter |
| `tests/unit/test_guardian_chunker.py` (modify) | Filter behaviour on the pure function |
| `tests/unit/test_guardian_chunked.py` (modify) | Fallback routing; empty-diff short-circuit preserved |

Task 1 is a pure refactor with no behaviour change — a reviewer can approve it on its own. Task 2 is the actual filter, and depends on Task 1's helper.

---

### Task 1: Extract the single-pass helper

**Files:**
- Modify: `src/cgis/guardian/chunked.py`

**Interfaces:**
- Produces: `_single_pass(provider: BaseProvider, collector: ContextCollector, skeptic_provider: BaseProvider | None) -> RoutedReview`

This task changes no behaviour. Its deliverable is that every existing test still passes while the single-pass construction lives in one place, ready for a second caller in Task 2.

- [ ] **Step 1: Add the helper**

In `src/cgis/guardian/chunked.py`, add above `run_chunked_review`:

```python
async def _single_pass(
    provider: BaseProvider,
    collector: ContextCollector,
    skeptic_provider: BaseProvider | None,
) -> RoutedReview:
    """Run the unchunked reviewer.

    Shared by the routing default and the no-reviewable-files fallback so the
    two cannot drift apart (#277). chunk_count is None, which is the recorded
    marker for "this review did not chunk".
    """
    reviewer = GuardianReviewer(
        provider=provider,
        context_collector=collector,
        skeptic_provider=skeptic_provider,
    )
    return RoutedReview(result=await reviewer.run_review(), chunk_count=None)
```

- [ ] **Step 2: Use it from the routing default**

In `run_review_routed`, replace the inline construction:

```python
    if not chunked:
        return await _single_pass(provider, collector, skeptic_provider)
```

(The `if chunked and (collector.db_path is None or not collector.db_path.exists())` guard above it, and its `log.warning`, stay exactly as they are.)

- [ ] **Step 3: Run the tests to verify nothing changed**

Run: `uv run pytest tests/unit/test_guardian_chunked.py -v`
Expected: PASS — every existing test, unchanged. This is a refactor; a failure here means the extraction altered behaviour.

- [ ] **Step 4: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/chunked.py
git commit -m "refactor(guardian): extract the single-pass helper (#277)"
```

---

### Task 2: Filter to reviewable source, fall back when nothing remains

**Files:**
- Modify: `src/cgis/guardian/chunker.py`
- Modify: `src/cgis/guardian/chunked.py`
- Test: `tests/unit/test_guardian_chunker.py` (append)
- Test: `tests/unit/test_guardian_chunked.py` (append)

**Interfaces:**
- Consumes: `_single_pass` from Task 1
- Produces: `build_chunks(diff_text: str, store: SQLiteStore | None, source_root: str = "", reviewable_suffixes: tuple[str, ...] = (".py",)) -> list[Chunk]`

- [ ] **Step 1: Write the failing chunker tests**

Append to `tests/unit/test_guardian_chunker.py`. The module already defines
`fdiff(path, body="+x = 1")` at the top — reuse it.

```python
def test_non_source_files_are_not_chunked() -> None:
    """A markdown chunk costs an API call and gets no context — it must not exist (#277)."""
    diff = fdiff("src/cgis/a.py") + fdiff("docs/specs/design.md") + fdiff("uv.lock")

    chunks = build_chunks(diff, None)

    assert [c.files for c in chunks] == [("src/cgis/a.py",)]


def test_reviewable_suffixes_is_a_real_parameter() -> None:
    """The language assumption is configurable, not welded into the body."""
    diff = fdiff("src/cgis/a.py") + fdiff("ui/src/app.ts")

    chunks = build_chunks(diff, None, reviewable_suffixes=(".py", ".ts"))

    assert sorted(f for c in chunks for f in c.files) == ["src/cgis/a.py", "ui/src/app.ts"]


def test_a_docs_file_cannot_be_pulled_into_a_source_chunk() -> None:
    """Filtering runs before pairing, so no heuristic can resurrect a dropped file."""
    diff = fdiff("tests/unit/test_core.py") + fdiff("src/cgis/core.py") + fdiff("src/cgis/core.md")

    chunks = build_chunks(diff, None)

    assert all("src/cgis/core.md" not in c.files for c in chunks)


def test_a_diff_of_only_non_source_yields_no_chunks() -> None:
    """The caller distinguishes this from an empty diff and falls back (see chunked.py)."""
    assert build_chunks(fdiff("README.md") + fdiff("uv.lock"), None) == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v -k "non_source or reviewable_suffixes or docs_file or only_non_source"`
Expected: FAIL — the first assertion returns three chunks, and `reviewable_suffixes` is an unexpected keyword argument.

- [ ] **Step 3: Implement the filter**

In `src/cgis/guardian/chunker.py`, change the signature and add the filter right
after the split:

```python
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
```

Everything below that line is unchanged — `files = sorted(blocks)` and the rest
already operate on `blocks`.

Note `str.endswith` accepts a tuple directly, so no loop is needed.

- [ ] **Step 4: Run the chunker tests**

Run: `uv run pytest tests/unit/test_guardian_chunker.py -v`
Expected: PASS — the four new tests plus every pre-existing one. Pre-existing
tests use `.py` paths, so the filter does not disturb them.

- [ ] **Step 5: Write the failing fallback tests**

Append to `tests/unit/test_guardian_chunked.py`. It defines its own
`fdiff` (line 84), `_finder_json` (line 89) and `_collector(tmp_path, diff,
with_db=True)` (line 115), and imports `StubProvider` from `guardian_stubs` —
reuse all four rather than adding new ones. The module marks async tests with
`@pytest.mark.asyncio` even though `asyncio_mode = auto` is set, so match that.

```python
@pytest.mark.asyncio
async def test_a_docs_only_diff_falls_back_to_single_pass(tmp_path: Path) -> None:
    """Such a PR is reviewed today by single-pass; filtering must not silence it (#277)."""
    diff = fdiff("docs/specs/design.md") + fdiff("README.md")
    provider = StubProvider([_finder_json("docs/specs/design.md")])
    collector = _collector(tmp_path, diff)
    collector.features = frozenset({"chunked"})

    routed = await run_review_routed(
        provider=provider, collector=collector, skeptic_provider=None
    )

    assert routed.chunk_count is None  # the single-pass marker
    assert len(provider.prompts) == 1


@pytest.mark.asyncio
async def test_an_empty_diff_still_short_circuits_for_free(tmp_path: Path) -> None:
    """No blocks at all is genuinely nothing — do not pay for a call to learn that."""
    provider = StubProvider([])

    routed = await run_chunked_review(
        provider=provider, collector=_collector(tmp_path, ""), skeptic_provider=None
    )

    assert routed.chunk_count == 0
    assert provider.prompts == []
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_chunked.py -v -k "docs_only or short_circuits"`
Expected: the docs-only test FAILS — today it returns `chunk_count == 0` with no
provider call, because an all-filtered diff is treated as an empty one. The
empty-diff test should already PASS; it is there to pin behaviour the next step
must not break.

- [ ] **Step 7: Implement the fallback**

In `src/cgis/guardian/chunked.py`, extend the chunker import to bring in the
splitter as well:

```python
from cgis.guardian.chunker import build_chunks, split_diff_by_file
```

Then replace the `if not chunks:` block inside `run_chunked_review`:

```python
    if not chunks:
        if not split_diff_by_file(diff):
            return RoutedReview(
                result=ReviewResult(findings=[], summary="Empty diff — nothing to review."),
                chunk_count=0,
            )
        # Blocks exist but none are reviewable source: a docs-only PR. Single
        # pass reviews it today, so returning "nothing to review" here would be
        # a silent regression — exactly the invisible-skip failure #277 is about.
        log.info("No reviewable source in the diff; falling back to single pass.")
        return await _single_pass(provider, collector, skeptic_provider)
```

Splitting twice is deliberate: it is pure string work on a diff already in
memory, and the alternative — threading an "was it empty" flag out of
`build_chunks` — would complicate a pure function to save nothing.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_chunked.py tests/unit/test_guardian_chunker.py -v`
Expected: PASS — both new tests plus every pre-existing chunker/chunked test.

- [ ] **Step 9: Confirm acceptance criterion 1 with the offline probe**

The spec's criterion is that the same three PRs now produce only source chunks.
Build a graph and re-measure — no API calls involved:

```bash
uv run cgis ingest ./src --source-root src -o /tmp/g277.db
uv run python -c "
import subprocess
from pathlib import Path
from cgis.guardian.chunker import build_chunks
from cgis.storage.sqlite_store import SQLiteStore

for label, rng in [('PR274','2e768ce^..2e768ce'), ('PR278','e2ee313^..e2ee313'), ('PR280','d4d8d1b^..d4d8d1b')]:
    base, _, head = rng.partition('..')
    diff = subprocess.run(['git','diff',f'{base}...{head}'], capture_output=True, text=True).stdout
    with SQLiteStore('/tmp/g277.db') as store:
        chunks = build_chunks(diff, store, source_root='src')
    non_py = [f for c in chunks for f in c.files if not f.endswith('.py')]
    print(f'{label}: {len(chunks)} chunks, non-.py files: {non_py}')
"
```

Expected: `PR274: 3 chunks`, `PR278: 2 chunks`, `PR280: 2 chunks`, and an empty
`non-.py files` list on all three — down from 6, 7 and 7. If a count differs,
report the actual numbers rather than adjusting the expectation.

- [ ] **Step 10: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/chunker.py src/cgis/guardian/chunked.py tests/unit/test_guardian_chunker.py tests/unit/test_guardian_chunked.py
git commit -m "feat(guardian): chunk only reviewable source (#277)"
```

---

## Definition of done

- `make format && make lint && make type-check && make pytest && make doc-coverage` all pass.
- `uv run mypy src` passes in a CI-shaped environment (`uv sync --group dev`, no guardian group).
- The probe in Task 2 Step 9 reports 3 / 2 / 2 chunks with no non-`.py` files.
- A docs-only diff routes to single pass (`chunk_count is None`), and a genuinely empty diff still short-circuits with `chunk_count == 0` and no provider call.
- `chunked = "chunked" in collector.features` is unchanged — routing is still flag-based.
- Every pre-existing chunker and chunked test passes unchanged.

## Not verifiable here

The recall comparison #277 actually asks for needs a provider key, which is not
available in this environment. It needs no new code: `GUARDIAN_FEATURES=chunked`
already reaches the collector (`guardian_bench.py:121` → `141`) and
`results.jsonl` records the features string, so the two arms are distinguishable.
After this lands, that experiment measures a configuration worth shipping instead
of one where two thirds of the calls review markdown.
