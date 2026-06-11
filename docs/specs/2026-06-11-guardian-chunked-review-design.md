# Guardian Chunked Review — Design (slice 2 of #154)

Date: 2026-06-11
Status: draft → review
Depends on: slice 1 (`src/cgis/guardian/chunker.py`, merged 6178bae), Plan 2 (merged 3d42815).

## 1. Problem

The finder LGTMs large PRs. Baseline evidence: recall = 0 on the 3 largest-diff
benchmark PRs (140/143/144, 36–62K prompt tokens), and the Plan-2 ablation showed
that ADDING context (full_files, 94K tokens) does not help — the failure mode is
attention dilution, not missing context. Hypothesis #154: split the PR into
connected subgraphs and give each finder pass a small, complete world — its own
diff, its own full files (the 120K-char budget is achievable per chunk), its own
impact graph.

Slice 1 delivered the pure chunker. Slice 2 wires it into the review loop behind
a feature flag and proves (or refutes) the hypothesis with a benchmark gate.

## 2. Decisions locked (user-confirmed 2026-06-11)

1. **Activation**: chunking runs whenever `GUARDIAN_FEATURES` contains `chunked`
   (and a graph DB is available — §4.1). No size threshold, no bin-packing.
   A single-chunk result degenerates to exactly one finder pass = status quo.
2. **Bench scope**: two-phase (§7). Phase 1 gates the feature; phase 2 compares
   finder models on large PRs only.
3. **Noise control**: no global cap after merge. The per-pass cap of 5 stays;
   the single skeptic pass and the bench noise gate are the control.
4. **Skeptic context**: only the diffs (and full files) of chunks that produced
   findings — not the full PR diff. Attention dilution hits the skeptic too.

## 3. Architecture

New module `src/cgis/guardian/chunked.py`; `core.py` keeps the single-pass path
unchanged. One small refactor in `core.py`: the finder pass becomes reusable.

```
runner.run_guardian / guardian_bench._run_one
  └── run_review_routed(provider, collector, skeptic_provider)   [chunked.py]
        ├── "chunked" ∉ collector.features  → GuardianReviewer.run_review()
        ├── db_path missing                 → GuardianReviewer.run_review()  (warn)
        └── else                            → run_chunked_review(...)
              ├── chunks = build_chunks(diff, store, source_root)   [slice 1]
              ├── chunks = _cap_chunks(chunks)                       (§4.3)
              ├── for chunk in chunks:                               (sequential)
              │     ctx    = collector.collect_for_chunk(chunk)      (§4.2)
              │     result = finder_pass(provider, ctx)              [core.py]
              │     keep   = [f for f in result.findings if f.file in chunk.files]
              ├── merged = dedup(all kept findings)                  (§4.4)
              ├── skeptic: ONE pass, context = chunks-with-findings  (§4.5)
              └── one ReviewResult → render / post / score as today
```

### 3.1 core.py refactor

`GuardianReviewer._finder_pass(context)` becomes a module-level coroutine:

```python
async def finder_pass(provider: BaseProvider, context: dict[str, str]) -> ReviewResult
```

(same body: structured call → sanitize → one retry → parse_failed fallback).
`GuardianReviewer` keeps its public surface; `run_review()` calls the free
function. `chunked.py` imports it — no duplicated retry logic.

### 3.2 Feature flag

`VALID_FEATURES` in collector.py gains `"chunked"`. Flag semantics:

- `chunked` **implies** per-chunk full_files, per-chunk graph context, AND the
  flow fallback (outbound dependency graph for new files, #94) — that IS the
  #154 hypothesis: each chunk gets a small, complete world. The `full_files` /
  `flow` / `drift` flags do not additionally alter the chunked path; drift is
  a global single section, not per-chunk, and is NOT included in chunked mode
  in this slice.
- Without `chunked`, behaviour is byte-identical to today (regression guard:
  existing tests must pass unmodified).

## 4. Components

### 4.1 Routing (`run_review_routed`)

```python
async def run_review_routed(
    *,
    provider: BaseProvider,
    collector: ContextCollector,
    skeptic_provider: BaseProvider | None,
) -> ReviewResult
```

- `"chunked" not in collector.features` → delegate to `GuardianReviewer`.
- `collector.db_path is None or not exists` → delegate + `log.warning`
  ("chunked requested but no graph DB; falling back to single pass").
  Rationale: without a graph, build_chunks degrades to all-isolated chunks =
  one API call per file with zero connectivity benefit — worse than status quo.
- Both `runner.run_guardian` and `guardian_bench._run_one` switch from
  constructing `GuardianReviewer` directly to calling `run_review_routed`.

### 4.2 Per-chunk context (`ContextCollector.collect_for_chunk`)

```python
def collect_for_chunk(self, chunk: Chunk) -> dict[str, str]
```

Returns the same shape `collect_all()` produces, restricted to the chunk:

| key             | content                                                        |
|-----------------|----------------------------------------------------------------|
| `diff`          | `chunk.diff` (already assembled by slice 1)                    |
| `contributing`  | shared, as today                                               |
| `ontology`      | shared, as today                                               |
| `graph_context` | impact graphs for `chunk.files` ∩ `.py` only                   |
| `full_files`    | full HEAD text for `chunk.files` ∩ `.py`, same per-file 1200-line cap and 120K-char budget — but the budget now applies **per chunk** |

Implementation: `collect_full_files` and `collect_graph_context` gain an
optional `files: list[str] | None = None` parameter (None = all changed files,
today's behaviour). `graph_stats` accumulates across chunks (sum of totals,
sum of with_graph) so the footer "graph N/M files" stays truthful.

### 4.3 Chunk-count guard (`_cap_chunks`)

`MAX_CHUNKS = 8`. If `build_chunks` returns more: keep the 7 largest chunks
(by `len(chunk.diff)`), merge the remainder into one overflow chunk
(files = sorted union, diff = concatenation in slice-1 chunk order), append it
last, `log.warning` with the merged count. Bounds API calls on pathological
PRs (50 isolated files ≠ 50 finder calls). Deterministic: ties in size break
by first file name.

### 4.4 Merge + dedup

- **Filter**: per chunk, keep only findings with `finding.file ∈ chunk.files`;
  dropped findings are logged (`log.warning`, file + title). The finder saw
  only its chunk's diff — an out-of-chunk file is a hallucination by
  construction (prompt rule 1 requires evidence verbatim in the diff section).
- **Dedup**: after concatenating per-chunk survivors, drop duplicates by key
  `(file, line, category)`, keeping the higher-confidence one. Chunks
  partition files, so cross-chunk duplicates are impossible after the filter;
  this is insurance against intra-pass duplicates. Order preserved otherwise.

### 4.5 Single skeptic pass

Unchanged `SKEPTIC_SYSTEM_PROMPT` / `build_skeptic_prompt` / `apply_verdicts`.
The context handed to `build_skeptic_prompt` is synthesized:

- `diff` = concatenation of `chunk.diff` for every chunk with ≥1 surviving
  finding;
- `full_files` = concatenation of those same chunks' full_files sections.

Skeptic failure policy identical to today: `skeptic_status="failed"`, findings
returned unverified. No findings after merge → skip the skeptic (as today).

### 4.6 Merged ReviewResult

- `findings` = deduped, skeptic-merged list.
- `summary` = one bullet per chunk:
  `- [a.py, b.py]: <chunk summary>` ; a chunk that failed to parse twice
  renders `- [a.py, b.py]: ⚠ finder output unparsable`.
- `parse_failed` = True **only if every chunk failed** (then `summary` is the
  bullets, findings empty — render/bench treat it as today's parse failure).
  Partial failures: `parse_failed=False`, failed chunks contribute 0 findings.
- `skeptic_status` from the single skeptic pass ("off"/"ok"/"failed").

### 4.7 Token usage accumulation (BaseProvider)

N finder calls break the current accounting (`provider.last_usage` = last call
only; the parse-retry already loses its first call's tokens today — latent bug).

`BaseProvider` gains:

```python
self.cumulative_usage: ProviderUsage = ProviderUsage()   # in __init__

def _record_usage(self, usage: ProviderUsage) -> None:
    self.last_usage = usage
    self.cumulative_usage = ProviderUsage(
        prompt_tokens=self.cumulative_usage.prompt_tokens + usage.prompt_tokens,
        completion_tokens=self.cumulative_usage.completion_tokens + usage.completion_tokens,
    )
```

Gemini/Mistral providers switch their direct `self.last_usage = ...` writes to
`self._record_usage(...)`. `runner.run_guardian` and `guardian_bench._run_one`
report `cumulative_usage` (footer + metrics + JSONL) instead of `last_usage`.
This also fixes the retry-loses-tokens bug for the unchunked path.

### 4.8 Metrics

`record_review` gains `chunk_count: int | None = None` (None = unchunked,
keeps existing JSONL lines parseable). Bench JSONL entry gains the same field.

## 5. Error handling

| failure                            | behaviour                                              |
|------------------------------------|--------------------------------------------------------|
| no graph DB with `chunked` on      | fall back to single pass, warn (§4.1)                  |
| `build_chunks` store error         | slice-1 contract: isolated chunks (still capped, §4.3) |
| empty diff → no chunks             | return `ReviewResult(findings=[], summary="Empty diff — nothing to review.")` without any LLM call |
| one chunk's finder call raises     | that chunk contributes 0 findings + ⚠ bullet; remaining chunks still run (per-chunk try/except) |
| every chunk fails to parse         | `parse_failed=True` (§4.6)                             |
| skeptic fails                      | unverified findings, `skeptic_status="failed"` (today's policy) |

A chunk whose files are all non-`.py` (docs-only chunk) still gets a finder
pass — its diff is reviewable text; `graph_context`/`full_files` are simply
empty for it.

## 6. Testing

Unit tests only (project convention), mocked providers:

1. `run_review_routed`: no flag → single-pass path; flag without DB → single
   pass + warning; flag with DB → chunked path. (Spy on which path ran.)
2. `collect_for_chunk`: restricts full_files and graph_context to chunk files;
   per-chunk budget independent; graph_stats accumulate.
3. `_cap_chunks`: ≤8 unchanged; 10 chunks → 7 + overflow, deterministic.
4. Filter: out-of-chunk finding dropped and logged.
5. Dedup: same (file,line,category) keeps higher confidence.
6. Merged summary format incl. ⚠ bullet; parse_failed only when all fail.
7. Single skeptic call receives only chunks-with-findings diffs; verdicts
   apply across chunk boundaries by merged index.
8. Empty diff → no LLM calls.
9. One chunk raising → others still reviewed.
10. `cumulative_usage`: sums across calls; retry case covered; unchunked
    runner path reports cumulative (regression for the latent bug).
11. Regression: with `chunked` absent, `run_review_routed` output ==
    `GuardianReviewer.run_review` output (same mocks).

## 7. Benchmark plan (gate before merge to prod config)

Baseline to beat (Plan 2, prod config: 2.5-flash finder + 3.5-flash skeptic):
mean recall ≈ 0.22, noise/PR ≈ 0.83, recall = 0 on PRs 140/143/144.

**Phase 1 — feature gate** (`GUARDIAN_FEATURES=chunked`, finder 2.5-flash,
skeptic 3.5-flash): all 6 PRs × 3 runs. Pass criteria:

- mean recall (6 PRs) ≥ 0.27 (baseline + 0.05), AND
- mean recall over {140, 143, 144} > 0 (the hypothesis must show on large PRs), AND
- mean noise/PR ≤ 1.5, AND
- 0 lost: no PR where chunked mean recall drops > 0.05 below its baseline mean.

Fail → flag stays off in prod; results + analysis recorded in the PR; slice 3
re-evaluated.

**Phase 2 — model matrix** (only if phase 1 passes): finder ∈
{gemini-3.5-flash, gemini-3.1-pro} × chunked, PRs {140, 143, 144} × 2 runs,
same skeptic. Decision rule: switch prod finder model only if a candidate
beats 2.5-flash×chunked on mean large-PR recall by ≥ 0.05 with noise/PR ≤ 1.5.

Budget: ~8 PLN headroom to the 30 PLN cap. Estimate: phase 1 ≈ 2–2.5 PLN
(token total per run comparable to baseline — same diff split across calls,
plus per-chunk full_files), phase 2 ≈ 2–3 PLN. Within budget; abort if
spend hits 6 PLN mid-bench.

Mechanics: bench already routes `GUARDIAN_FEATURES` through `parse_features`
and (after §4.1) through `run_review_routed` — no new bench flags needed.
Model selection via existing `GUARDIAN_MODEL` env.

## 8. Out of scope (slice 3+ / explicitly not now)

- Quotient-level "architectural chunk" (cross-chunk findings via drift k=1).
- Concurrent chunk calls (sequential is fine at ≤8 chunks; revisit on latency
  complaints).
- Bin-packing of small chunks; per-chunk drift section; ensemble runs (#155).
- Prod config change: `GUARDIAN_FEATURES=chunked` repo var is set ONLY after
  the phase-1 gate passes and the PR is merged.
