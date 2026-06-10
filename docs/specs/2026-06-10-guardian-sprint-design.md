# Guardian Sprint — Structured Findings, Benchmark, Context, Multi-Pass, Inline

**Status:** Draft — for review
**Date:** 2026-06-10
**Motivation:** gemini-code-assist (the current external PR reviewer) sunsets
2026-07-17 (new installs blocked 06-18). The in-repo Guardian becomes the
primary reviewer. This sprint closes the quality gap — measurably.
**Depends on:** merged motif-basis epic (8a63efe) for the drift section;
existing guardian subsystem (`src/cgis/guardian/`).
**Related issues:** #126 (diff-only hunks), #94 (empty impact graph for new
files), #146/#151 (drift enforcement — soft path opened here).

---

## 1. Context and Motivation

The Guardian today: `/guardian review` PR comment triggers
`.github/workflows/guardian.yml`, which ingests `src/` into `graph.db`, runs
`scripts/guardian_review.py` (collector → prompt → one LLM call → markdown),
and posts one big PR comment via peter-evans. Providers: gemini-2.5-flash and
mistral-medium-latest, selected by `GUARDIAN_PROVIDER`.

Known gaps, each observed on real PRs:

- **Diff-only hunks (#126):** the LLM sees only diff context lines; on PR #122
  gemini caught a defect guardian physically could not see.
- **New files get no graph context (#94):** `get_impact_graph` is empty for a
  file nothing depends on yet.
- **One monolithic comment:** findings are not anchored to lines; harder to
  act on than gemini's inline threads.
- **Unmeasured quality:** we have anecdotes ("guardian gave a substantive LGTM
  on #144"), not numbers. Without a benchmark, every prompt tweak is a guess.
- **Drift unused:** the motif-basis machinery (drift v2, quotient) is not fed
  to the reviewer at all.

### Locked decisions (user-confirmed)

1. **Success criterion = benchmark on past PRs.** Replay guardian on 6–8
   merged PRs; ground truth hand-curated per PR from gemini threads, Sonar
   alerts, and review-fix commits. Metrics: recall vs ground truth + absolute
   noise count. Baseline is measured **before** any improvement lands.
2. **Scope = all four improvements:** context upgrades (#126 full files, #94
   flow-fallback, drift section), inline comments (GitHub Review API),
   multi-pass verification (cross-provider skeptic), all on top of structured
   findings.
3. **Providers stay as-is:** gemini-2.5-flash + mistral-medium-latest. The
   skeptic is always the *other* provider (different blind spots). No new
   providers this sprint.
4. **Architecture = structured findings JSON as the core contract** (Approach
   A). One schema feeds the markdown report (back-compat), inline comments,
   benchmark scoring, and multi-pass. Build order: structured output →
   benchmark harness + baseline → context upgrades → multi-pass. Every step is
   measured against the baseline.

### Non-goals

- New LLM providers, model upgrades, or prompt-engineering beyond what the
  new sections require.
- A "second finder" pass that hunts for bugs the first pass missed (grows
  noise; revisit only if the benchmark shows recall — not noise — is the
  bottleneck).
- Resolving/updating old review threads on re-runs.
- Hard CI enforcement of drift (#146/#151 stay separate; guardian flags drift
  as an advisory finding only).

---

## 2. Structured Findings — the Core Contract

### 2.1 Models

New module `src/cgis/guardian/findings.py`, frozen Pydantic (project-wide
convention):

```python
class Finding(BaseModel, frozen=True):
    file: str                  # path relative to repo root
    line: int | None = Field(default=None, gt=0)  # HEAD line; None = file-level
    severity: Literal["critical", "major", "minor"]
    category: Literal["logic", "contract", "tests", "types", "ontology"]
    title: str                 # short headline
    evidence: str              # verbatim quote from the diff (existing rule)
    problem: str               # one sentence
    fix: str                   # concrete suggestion
    confidence: int = Field(ge=0, le=100)  # the >= 80 gate stays
    verdict: Literal["confirmed", "refuted", "uncertain"] | None = None
    skeptic_note: str | None = None   # filled by multi-pass (section 5)

class ReviewResult(BaseModel, frozen=True):
    findings: list[Finding]    # empty list = LGTM
    summary: str               # 2–3 checked-and-correct aspects (LGTM text)
    parse_failed: bool = False # raw-text fallback marker (section 2.3)
```

The five `category` values map 1:1 to the prompt's existing focus areas
(Logic Bug / Library Contract / Test Coverage / Type Safety / Ontology).

### 2.2 Provider contract

Both providers support native JSON mode: gemini via
`response_mime_type="application/json"` + `response_schema`, mistral via
`response_format={"type": "json_object"}`. `BaseProvider` gains:

```python
@abc.abstractmethod
async def generate_structured(
    self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
) -> str:
    """Send a prompt requesting JSON conforming to schema; return raw JSON text."""
```

Providers return *raw JSON text*. Parsing and validation
(`ReviewResult.model_validate_json`) live in `GuardianReviewer`, not in the
providers — providers stay thin transport.

### 2.3 Parse-failure policy

On validation error: **one retry**, appending the validation error text to the
conversation. On second failure: the entire raw response becomes
`ReviewResult.summary`, `findings=[]`, `parse_failed=True`. The flag is
recorded in metrics so the benchmark sees parse failures instead of mistaking
them for clean LGTMs.

### 2.4 Prompt change

Only the OUTPUT FORMAT section of the user prompt changes: the markdown
template is replaced by the JSON schema plus one worked example. All PRECISION
RULES, focus areas, and the 5-finding cap stay verbatim.

### 2.5 Rendering

`src/cgis/guardian/render.py` — a pure function
`render_report(result: ReviewResult) -> str` producing markdown visually
matching today's report. The workflow and peter-evans step are untouched at
this stage.

**Breaking change (internal only):** `GuardianReviewer.run_review()` returns
`ReviewResult` instead of `str`. Consumers: `scripts/guardian_review.py`
(calls `render_report` itself) and tests.

---

## 3. Benchmark Harness

### 3.1 Ground truth format

One file per PR: `benchmarks/guardian/pr-<N>.yaml`, hand-curated (~1h each,
sanctioned) from gemini threads, Sonar alerts, and review-fix commits on 6–8
merged PRs (#122, #140–#144, plus 1–2 older ones).

```yaml
pr: 144
base: <sha>            # merge-base at PR time
head: <sha>            # from refs/pull/144/head — survives squash-merge
findings:
  - id: float-equality-tests
    file: tests/unit/test_quotient.py
    lines: [60, 75]    # acceptable line range in the HEAD version; optional
    severity: major
    category: tests
    summary: "direct == on floats in asserts"
    source: sonar      # gemini | sonar | fix-commit | human
ambiguous:             # neither a miss nor noise
  - file: src/cgis/query/drift.py
    summary: "clip-to-[0,1] (gemini proposed it; we declined with reasons)"
```

The `ambiguous` section is essential: suggestions we declined with technical
reasoning are not ground truth, but penalizing guardian for raising them as
noise would be unfair.

### 3.2 Matching (deterministic, pure, unit-tested)

A predicted finding matches a ground-truth entry iff same `file` AND (`line`
within the entry's `lines` range, or the entry has no `lines`). Category is
**not** required for a match — models categorize differently; the mismatch is
recorded in the report but the match counts. Each ground-truth entry matches
at most once (greedy, descending predicted confidence).

### 3.3 Metrics

- `recall` = matched / total ground truth (overall + per severity)
- `noise` = predictions matching neither ground truth nor `ambiguous`
  (absolute count — the primary noise metric)
- `precision` = matched / predictions

Reported per PR and aggregated.

### 3.4 Runner

`scripts/guardian_bench.py` (entry); matching/scoring logic in
`src/cgis/guardian/bench.py` under unit tests. Per PR:

1. `git fetch origin pull/N/head` → detached worktree at `head`. The runner
   requires full history (it refuses to run in a shallow clone): the
   merge-base `base` must be resolvable locally or step 2 fails.
2. diff = `git diff base...head` — exactly what CI would have seen.
3. `cgis ingest src` inside the worktree → `graph.db` (replicating the
   workflow's conditions).
4. `GuardianReviewer.run_review()` → `ReviewResult` (this is why structured
   output ships first — the scorer consumes JSON, never parses markdown).
5. Score → append one line to `benchmarks/guardian/results.jsonl`: timestamp,
   provider, guardian-code git SHA, enabled features, metrics, raw findings.

Flags: `--pr 144` (single PR; default all), `--runs N` (LLM nondeterminism —
baseline at N=3, later steps N=1 with the measured variance in mind),
`--features ...` (ablation, see section 4).

`results.jsonl` is **committed** — the measurement history is part of the
repo.

### 3.5 Baseline protocol

Immediately after structured output merges: run the bench on the current
prompt/context with **both** providers. That snapshot is the oven every
subsequent step is measured against.

---

## 4. Context Collector Upgrades

Three additions. Each is a separate key in `collect_all()`, a separate prompt
section, and **individually flagged** (constructor
`ContextCollector(features=...)` + `GUARDIAN_FEATURES` env). Flags exist for
the benchmark's sake: ablation runs (baseline / +full_files / +flow / +drift)
isolate each addition's contribution, so sections that add tokens without
adding recall get dropped.

### 4.1 Full file contents (#126)

New prompt section `FULL FILE CONTENTS (HEAD)`: the complete HEAD text of
every changed `.py` file. Budgets:

- per-file cap ~1200 lines — larger files stay diff-only, with an explicit
  `file omitted: too large` note *inside the section*;
- global cap ~120K characters, filled smallest-file-first (whole small files
  beat half a giant).

The omission notes prevent the model from reading absence-of-file as
absence-of-code.

### 4.2 Flow-fallback for new files (#94)

In `collect_graph_context()`: when `get_impact_graph` returns empty (a new
file — nothing references it yet), fall back to `get_flow_graph` (what does
it call) and title the section "Dependency graph (outbound)" instead of
"Impact graph". `graph_stats` gains a fallback counter.

### 4.3 Architectural drift section

After ingest, `graph.db` and `patterns.yaml` are both present — call
`FingerprintExtractor` + `DriftScorer` programmatically (no subprocess) and
render a compact table: domain, drift, tolerance, `⚠` marker when exceeded,
plus the quotient k=1 line. Prompt section `ARCHITECTURAL DRIFT (motif-basis)`
with a two-line reading instruction: a PR pushing a domain past tolerance is
an `ontology`-category finding; the observe-only quotient must not be
flagged.

This is the first real consumer of drift v2 outside tests: the soft
enforcement channel deferred in #146/#151 (an LLM finding, not a CI failure).

### 4.4 Degradation

All graceful, as today: no `graph.db` → no graph/drift sections; no
`patterns.yaml` → no drift section. The collector never fails a review over
missing context.

---

## 5. Multi-Pass — Cross-Provider Skeptic

### 5.1 Shape: finder → skeptic

Pass 1 — the primary provider (`GUARDIAN_PROVIDER`) produces a
`ReviewResult`. If findings are non-empty, pass 2 — the **other** provider
(gemini found → mistral checks, and vice versa) receives the same context
plus the findings list, with a refutation stance: *"try to REFUTE each
finding against the diff; if uncertain, refute."* The inverted default is the
filter against plausible-but-wrong findings — the main noise source.

### 5.2 One skeptic call, not N

Context (diff + full files) is large and shared; findings are capped at 5.
Per-finding calls would multiply tokens ~5× for questionable independence
gains. Response schema:

```python
class SkepticVerdict(BaseModel, frozen=True):
    finding_index: int
    verdict: Literal["confirmed", "refuted", "uncertain"]
    rationale: str

class SkepticResult(BaseModel, frozen=True):
    verdicts: list[SkepticVerdict]
```

The merge step validates `finding_index` against the pass-1 findings list:
out-of-range or duplicate indices are discarded and logged. A finding the
skeptic never ruled on keeps `verdict=None` and is **not** filtered — absence
of a verdict is not a refutation.

### 5.3 Filtering

- `refuted` → dropped from the report, **but recorded in metrics JSONL** —
  the benchmark must see whether the skeptic kills true positives (the main
  multi-pass risk).
- `confirmed` → kept; the rendered finding gains a `Verified by <provider>`
  line.
- `uncertain` → kept with a note and an integer confidence discount
  (`round(confidence * 0.9)` — the field is `int`); if the result drops below
  80, treated as refuted. The multiplier is 0.9, not lower: the finder's gate
  guarantees confidence ∈ [80, 100], so ×0.9 keeps exactly the
  originally-high-confidence findings (≥ 89) and refutes the rest — a smaller
  multiplier would refute *every* uncertain finding and make this branch dead
  code.

Verdicts are written via `Finding.model_copy(update={...})` — the model is
frozen, so the skeptic pass produces new `Finding` instances rather than
mutating pass-1 results (fields default to `None`, so single-pass results
remain valid).

### 5.4 What the skeptic does NOT do

It does not hunt for new bugs (that is a second finder — the opposite of the
goal) and it is not called on an empty pass 1 (an LGTM has nothing to
refute).

### 5.5 Config and degradation

`GUARDIAN_SKEPTIC=mistral|gemini|off`; default = the provider opposite to the
primary. Missing API key or skeptic failure → graceful single-pass with a
note in the report and metrics. A review never fails because of the skeptic.

### 5.6 Benchmark gate for this step

Noise must drop while recall holds. "Holds" is absolute, not percentage —
the ground truth is small: across the whole benchmark set, multi-pass may
lose **at most one** previously-matched ground-truth finding. If the skeptic
cuts more, the first knob is its stance (refute-by-default →
confirm-by-default) — the most sensitive dial in this design.

---

## 6. Inline Comments — GitHub Review API

### 6.1 One review, not scattered comments

`POST /repos/{owner}/{repo}/pulls/{N}/reviews` with `event: COMMENT` and
`comments: [{path, line, side: RIGHT, body}]` — findings arrive atomically,
like gemini's. Always `COMMENT`, never `REQUEST_CHANGES`: guardian is an
advisor, not a gate (same observe-only philosophy as the quotient).

### 6.2 The valid-lines problem

GitHub only accepts inline comments on lines present in the diff. With
full-file context (section 4.1), findings may legitimately point outside the
hunks — a feature, not a bug. Solution: a pure function
`diff_line_index(diff_text) -> dict[str, set[int]]` parsing hunk headers into
valid RIGHT-side lines (unit-tested, no network). The parser strips git's
`a/`/`b/` prefixes and keys renames by the *new* path, so keys match
`Finding.file` (repo-relative) exactly. A finding whose line is in
the index → inline comment; outside the index or `line=None` → a "Findings
outside the diff" section in the review body. Nothing is lost, nothing
crashes.

### 6.3 Rendering

Inline comment body = one finding: severity marker (🔴 critical / 🟠 major /
🟡 minor), category, problem, fix, `Verified by <skeptic>` when present.
Review body = summary + out-of-diff findings + LGTM text when the findings
list is empty. No GitHub suggestion blocks — `fix` is prose, not a patch
(YAGNI).

### 6.4 Posting

New `src/cgis/guardian/github_poster.py`, shelling out to `gh api`
(preinstalled in Actions, auto-auth via `GITHUB_TOKEN`, zero new
dependencies; subprocess is mocked in tests). Repo and PR number come from
`GITHUB_REPOSITORY` / the event payload env, as the workflow does today.

### 6.5 Fallback chain in the workflow

The script attempts the inline review → on any API error it writes
`report.md` and sets an output flag → the existing peter-evans step posts the
big comment as before (the step becomes conditional:
`if: steps.guardian.outputs.inline_failed`). The old path is not deleted
until inline has worked on a couple of live PRs.

### 6.6 Re-runs

A repeated `/guardian review` creates a new review (gemini behaves the same).
Resolving old threads is out of scope.

---

## 7. Error Handling — Cross-Cutting Rules

One principle for the whole sprint: **guardian is an advisor — it may never
fail CI, and no degradation is silent** (every one is visible in the report
and in metrics JSONL).

| Failure | Behaviour |
| --- | --- |
| LLM returns invalid JSON | 1 retry with the validation error → fallback: raw text into `summary`, `parse_failed=True` in metrics |
| Skeptic provider unavailable / fails | single-pass, noted in report and metrics |
| Inline review rejected by API | output flag → peter-evans posts `report.md` (old path) |
| Missing `graph.db` / `patterns.yaml` / CONTRIBUTING | context section skipped, `graph_stats`-style counters |
| Primary provider fails entirely | comment "Guardian failed: <reason>", **exit 0** — workflow stays green, review never blocks merge |
| Bench: one PR fails to replay (fetch/ingest) | `error` line in results.jsonl, remaining PRs continue; non-zero exit only on harness-level failure |

Exceptions are caught at subsystem boundaries (provider, poster, a collector
section, a bench iteration) — inner logic stays pure and raises honestly, as
this repo already does.

---

## 8. Testing Strategy

**Unit (the bulk — pure functions, no network):**

- `findings.py`: schema validation, the parse-retry path, the `parse_failed`
  fallback.
- `render.py`: golden tests for the markdown render (full report, LGTM,
  finding with skeptic verdict, out-of-diff findings).
- `diff_line_index`: hunk parsing — multiple files, renames, new file, empty
  diff.
- `bench.py`: matching/scoring — exact match, line range, ambiguous,
  greedy single-use, metric arithmetic.
- Skeptic merge logic: refuted-drop, uncertain-discount, the 80 boundary.
- Collector: new sections against a tmp repo + fake graph.db (existing
  collector-test pattern), feature flags, full-file caps, flow-fallback.
- `github_poster`: mocked subprocess, payload correctness, fallback flag.

**Closing an existing gap:** `scripts/guardian_review.py` is untested today —
refactor into a testable `main()` with injected reviewer/poster, plus a smoke
test using a `FakeProvider` (canned JSON → `ReviewResult` → render → poster
dry-run). That smoke test is the single "integration" test.

**No live LLMs in CI** — the live validation *is* the benchmark, run
manually. Provider `generate_structured` implementations are tested with
mocked SDK clients.

---

## 9. Build Order and Measurement Discipline

1. **Structured output** (section 2) — the contract everything else consumes.
2. **Benchmark harness + ground truth + baseline** (section 3) — both
   providers, N=3 runs, committed to `results.jsonl`.
3. **Context upgrades** (section 4) — measured as ablations vs baseline.
4. **Multi-pass skeptic** (section 5) — gated on noise↓ with recall held.
5. **Inline comments** (section 6) — presentation layer; no benchmark gate
   (the metrics don't change, the delivery does).

A step that fails its benchmark gate does not advance to the next; we tune or
drop the feature flag instead. The flags make "drop" a one-line config
change, not a revert.
