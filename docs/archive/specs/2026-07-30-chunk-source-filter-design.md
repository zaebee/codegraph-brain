# Chunked review: only chunk reviewable source — design (#277)

**Status:** approved (2026-07-30)
**Issue:** #277
**Lane:** guardian

## Goal

Stop the chunked review path from spending an API call per markdown file. Routing
stays flag-based; this only fixes *what* gets chunked, so that the bench #277 asks
for measures the configuration someone would actually enable.

## What the measurement showed

`build_chunks` is pure logic, so the cost side of #277 can be measured with no API
calls at all. Run over three merged PRs from this repo, with a real graph DB:

| PR | chunks | source | non-source | non-source share of the diff |
|----|--------|--------|------------|------------------------------|
| #274 | 6 | 3 | **3** | 53% |
| #278 | 7 | 2 | **5** | 67% |
| #280 | 7 | 2 | **5** | 65% |

Chunking would cost 6–7 API calls where single-pass costs 1, and **the majority of
those calls review documentation** — spec markdown, `uv.lock`, `pyproject.toml`,
`.pre-commit-config.yaml`.

The chunker's core works: graph connectivity correctly grouped
`mcp_server.py + cli.py + fractal.py` into one chunk on #274. The waste is entirely
in *which files* reach it.

### Docs chunks are not merely extra — they are degenerate

`collect_for_chunk` already narrows to Python before building context:

```python
py_files = [f for f in chunk.files if f.endswith(".py")]
```

A markdown-only chunk therefore gets no `full_files` and no graph context. The call
is spent asking a code reviewer to review a spec document **blind**, with only
`CONTRIBUTING.md` and the ontology yaml for company. That is not a weak review; it
is a call that cannot succeed at the thing guardian is for.

### Why this must precede the bench

#277 asks for a recall comparison between paths. Benching the current chunker would
measure an arm where two thirds of the calls review documentation — a configuration
nobody would ship. The answer would be real and useless.

## Design

### Filter

`build_chunks` gains a parameter:

```python
def build_chunks(
    diff_text: str,
    store: SQLiteStore | None,
    source_root: str = "",
    reviewable_suffixes: tuple[str, ...] = (".py",),
) -> list[Chunk]:
```

Blocks whose file does not end in a reviewable suffix are dropped immediately after
`split_diff_by_file`, before union-find grouping — so a docs file cannot even be
pulled into a source chunk by the test-pairing or graph heuristics.

A parameter rather than a hardcoded constant: the language assumption becomes
visible in the signature and pinned by a test, instead of hiding inside the body.

### Why `.py`

Not invented here. It is the notion of "source" the collector already uses in two
places: `get_changed_py_files` (`collector.py:117`) and the `py_files` narrowing in
`collect_for_chunk` (`collector.py:295`). Widening to `.ts`/`.tsx` would create the
appearance of TypeScript support without the substance, since `collect_for_chunk`
would still hand those chunks an empty context.

### Two empty cases, deliberately different

| situation | behaviour | why |
|-----------|-----------|-----|
| the diff has no file blocks at all | keep today's free short-circuit: `chunk_count=0`, "Empty diff — nothing to review." | there is genuinely nothing; paying for a call would be silly |
| the diff has blocks, but none reviewable (docs-only PR, TS-only PR) | **fall back to single pass** | today such a PR *is* reviewed, by single-pass. Returning "nothing to review" would be a silent regression |

The fallback follows the precedent three lines above it — `chunked` requested
without a graph DB already degrades to single pass rather than failing.

Without it, the filter would quietly create the failure mode #277 exists to
document: a path that looks like it ran and reviewed nothing.

### Implementation shape

`run_chunked_review` keeps its signature — it has six call sites in
`test_guardian_chunked.py`, and churning them for a path that has never run in
production is not worth it. Instead the single-pass construction moves into a
helper both branches share:

```python
async def _single_pass(
    provider: BaseProvider,
    collector: ContextCollector,
    skeptic_provider: BaseProvider | None,
) -> RoutedReview:
    """Run the unchunked reviewer. Shared by the routing default and the
    no-reviewable-files fallback so the two cannot drift apart."""
```

`run_review_routed` uses it for the not-chunked case; `run_chunked_review` uses it
when filtering leaves nothing. No duplication, no signature change.

## Known limitation, stated rather than discovered

**In chunked mode, non-Python files are not reviewed at all.** Today that costs
nothing: the path is dead (41/41 recorded runs are single-pass), and those files
already received no context. But if chunking is ever enabled on a repo whose
changes are TypeScript, review coverage silently drops to the Python subset — the
`reviewable_suffixes` parameter is where that gets fixed, and the fallback above
keeps a fully-TS PR from vanishing entirely.

## What this does not do

- **Routing is untouched.** `chunked = "chunked" in collector.features` stays exactly
  as it is. Enabling chunking still requires the recall evidence #277 asks for.
- No prompt, skeptic or scoring change.

## Error and edge handling

- A file with no extension, or a path ending in a suffix that merely contains `.py`
  (e.g. `notes.python`): `str.endswith(".py")` is exact, so these are dropped. That
  is correct — they are not Python modules.
- Filtering runs before `_test_pairs` and `_graph_pairs`, so neither can resurrect a
  dropped file.
- `MAX_CHUNKS` capping is unaffected; it now caps a smaller, more useful set.

## Testing

Unit (`tests/unit/test_guardian_chunker.py` and `test_guardian_chunked.py`):

- A diff of one `.py` and two `.md` files yields exactly one chunk, containing only
  the `.py` file.
- `reviewable_suffixes=(".py", ".ts")` keeps a `.ts` file — the parameter is real,
  not decorative.
- A docs file that would pair with a source file by name never joins its chunk.
- A docs-only diff routes to single pass: with `chunked` in features and a graph DB
  present, the reviewer runs once and `chunk_count is None`.
- A genuinely empty diff still short-circuits with `chunk_count == 0` and makes no
  provider call.

## Acceptance criteria

1. Re-running the offline chunk probe on #274/#278/#280 shows only source chunks:
   3, 2 and 2 respectively, down from 6, 7 and 7.
2. `make format && make lint && make type-check && make pytest && make doc-coverage`
   all pass, and every existing chunker/chunked test passes unchanged except where
   the filter is the point.

## Out of scope

- The recall bench itself (#277's main question). It needs a provider key, and the
  harness already supports both arms — `GUARDIAN_FEATURES=chunked` reaches the
  collector, and `results.jsonl` records the features string, so the two arms are
  distinguishable with no new code.
- Enabling chunking by size or by default.
