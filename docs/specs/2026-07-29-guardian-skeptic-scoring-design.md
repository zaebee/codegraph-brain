# Guardian Skeptic — Per-Finding Judgement and Impact Scoring (#246)

**Status:** proposed design
**Issue:** #246 — guardian: cross-model skeptic to cut recall-lean noise without killing recall
**Date:** 2026-07-29
**Amends:** `docs/specs/2026-06-10-guardian-sprint-design.md` §5.2 ("one call, not N")

## 1. Problem

The recall-lean finder shipped in #249 works: PR 144 recall went 0/5 → median
3/5. It buys that recall by emitting 10–26 findings where the old gated finder
emitted ≤5, and it explicitly delegates precision to the skeptic pass.

The skeptic has not held up its end. Measured behaviour:

- **Same-family skeptic is binary.** mistral finder + mistral/codestral skeptic
  does not cut noise (post-reconciliation noise stayed ~12–17, roughly
  finder-only). Adding few-shot false-positive examples swung it the other way —
  refute-all, pr-143 recall 0.7 → 0.0 — and was reverted.
- **It cannot rank.** Live run on PR #263 (2026-07-29, prod config
  mistral-medium finder + codestral skeptic): 8 findings, **all 8 confirmed
  uniformly at the same severity**. One was a real defect (`Path.is_file()`
  raises on EACCES/ENAMETOOLONG, so a new candidate loop violated the module's
  documented "degrades, never raises" contract — accepted and fixed). The
  other seven were declined, including two that were factually wrong
  (`for size: int in range(...)` is not valid Python; `as_posix()` would have
  broken the very Windows-path case the line exists for). The reviewer output
  gave a human no way to tell these apart.

Two independent causes sit behind that, and only one of them is the model:

1. **Mechanism.** The skeptic makes exactly one call carrying the whole findings
   list (`build_skeptic_prompt(context, findings)`, verdicts mapped back by
   `finding_index`). Every finding competes for attention in one window, and
   index mapping adds its own failure modes — out-of-range and duplicate indices
   are both handled today by discarding the verdict.
2. **Contract.** `Verdict = Literal["confirmed", "refuted", "uncertain"]` can
   express "this claim is false". It cannot express "this claim is true and
   worthless". Both are noise to a reader; only the first is refutable. A
   three-value enum also cannot be sorted or thresholded.

Swapping the skeptic model underneath an unchanged mechanism would produce the
same flat output at higher cost. This spec fixes the mechanism first; the model
matrix (the original framing of #246) runs on top of it as phase 2.

## 2. Scope

**In scope:** the skeptic pass — `skeptic.py`, its call site in `core.py`, the
`Finding` contract, report ordering, and the benchmark methodology needed to
evaluate any of it.

**Out of scope:**

- Finder prompt changes. The recall-lean finder is measured and stays as is.
  Precision belongs to the skeptic by design (#249).
- Chunked review (#154). `chunked.py` is ported to the new API mechanically so
  a second, diverging skeptic call site does not survive, but its flag stays off
  and it is not re-benched here.
- Blind classes (#247), long-output robustness (#248), call-site graph context.
  The last is attractive and related; it is deliberately left out so this change
  moves one variable.

## 3. Design

### 3.1 Contract: two axes

The skeptic answers two orthogonal questions per finding:

| Axis | Field | Question | Effect |
|---|---|---|---|
| Truth | `verdict` (unchanged) | Is this claim true? | `refuted` drops the finding |
| Importance | `impact_score: int` 0–10 (new) | Does it matter? | Below threshold → hidden, not dropped |

```python
class FindingJudgement(BaseModel, frozen=True):
    verdict: Literal["confirmed", "refuted", "uncertain"]
    impact_score: int = Field(ge=0, le=10)
    rationale: str
```

`Finding` gains `impact_score: int | None = None` alongside the existing
`verdict` / `skeptic_note`.

`finding_index` disappears. Each judgement belongs to the call that produced it,
so the index-mapping failure modes (out-of-range, duplicate) cease to exist
rather than being handled.

Existing merge semantics are preserved exactly: `refuted` drops, `uncertain`
keeps the finding and discounts confidence ×0.9 as a ranking signal, a missing
judgement is **not** a refutation.

### 3.2 API

```python
async def judge_finding(
    provider: BaseProvider, finding: Finding, hunks: str
) -> FindingJudgement | None       # None = this call failed; finding survives unruled

async def judge_all(
    provider: BaseProvider, findings: list[Finding], diff: str, concurrency: int
) -> list[FindingJudgement | None]  # positional, one per finding

def apply_judgements(
    findings: list[Finding], judgements: list[FindingJudgement | None]
) -> list[Finding]                  # pure merge, unit-testable without a provider
```

`visible_findings(findings, threshold=0)` hides `refuted` **and** anything whose
`impact_score` is below `threshold`. Findings hidden by the threshold stay in the
JSONL record — otherwise the benchmark cannot distinguish "noise fell" from
"we went blind".

### 3.3 Prompt and rubric

**The truth axis is not touched.** The current confirm-by-default system prompt
("refute ONLY when you can point to concrete evidence that it is wrong") was
bought by reverting a refute-by-default version that killed 7/7 findings
including 2 ground-truth matches. It is reused verbatim.

**The importance axis is new, and it is what makes a previously-refuted lever
usable.** Few-shot false-positive examples (research lever #4) were tried and
reverted because on a single axis the only way to express "bad finding" is
refutation, and mistral-medium overshot into refute-all. Calibration examples
now land on `impact_score`, structurally unable to reach the refutation logic.

Rubric shipped in the prompt with short anchors:

| Score | Meaning |
|---|---|
| 0–2 | True but not actionable: style, taste, "consider X for explicitness", restating what tooling already enforces |
| 3–5 | Minor real issue: local clarity or robustness, no behaviour change |
| 6–8 | Real defect with a concrete failure path in this diff |
| 9–10 | Contract break, security, data loss |

**Tooling rule:** "if `ruff`, `ruff format` or `mypy --strict` would catch it,
`impact_score` ≤ 2." This is Gemini-review advice placed on the axis where it is
safe. In the *finder* prompt the same instruction would re-introduce the
precision bias that caused recall 0/5 (#249). As a rubric anchor it is also
objectively checkable here: both tools are mandatory gates in this repo, so what
they catch is covered by construction. The PR #263 run produced exactly this
case — a finding asserting a strict-mypy violation in a file where
`mypy --strict` is green.

**The skeptic does not see the finder's `confidence` or `severity`.** Both are
the finder's own guess at the quantity being re-derived; showing them anchors
the judge on the defendant. On PR #263 all eight findings arrived at one
severity and left at one severity.

**Context per call:** the finding (`title`, `evidence`, `problem`, `fix`, file
and line) plus the hunks of its own file, from `diff_index`. The prompt states
explicitly that a claim about code outside the supplied hunks **cannot be
checked**, which is grounds for `uncertain` and never for `refuted` — otherwise
narrowing the context becomes a false-refutation generator and reproduces
refute-all by another route.

### 3.4 Execution and degradation

`core.py::run_review` replaces its single `generate_structured` call with
`judge_all`: `asyncio.gather` over per-finding calls behind a semaphore sized by
`GUARDIAN_SKEPTIC_CONCURRENCY` (default 3 — mistral's free tier enforces a
per-minute token cap, and a local ollama skeptic serialises on one model
instance anyway).

Degradation stops being all-or-nothing. `skeptic_status` gains `partial`:

| Status | Meaning |
|---|---|
| `off` | No skeptic configured |
| `ok` | Every finding judged |
| `partial` | Some calls failed; `judged/total` reported in the footer and JSONL |
| `failed` | No finding was judged |

Unjudged findings stay visible. A per-call failure now costs one verdict instead
of all of them — today a single batch parse failure discards the entire pass.

`_sanitize_finder_result` must wipe `impact_score` along with `verdict` and
`skeptic_note`. The field lives on the shared `Finding` model, so the finder
sees it in the schema and can hallucinate it — the same defect class as the
Plan 2 bug where a hallucinated `verdict="refuted"` silently dropped findings
through `visible_findings`. Via the threshold the failure would be quieter still:
not "refuted" but merely "unimportant". A test pins this.

### 3.5 Rendering and configuration

`render.py` orders findings by `impact_score` descending (currently
critical→major→minor) and shows the score next to each finding.
`GUARDIAN_IMPACT_THRESHOLD` defaults to **0**, i.e. nothing is hidden on day
one. The knob is turned only after the benchmark shows the score distribution —
PR-Agent's equivalent documents that a threshold above 8 clips genuinely useful
suggestions, and we have no reason to assume our calibration matches theirs.

### 3.6 Spec amendment

`2026-06-10-guardian-sprint-design.md` §5.2 specifies one skeptic call, not N.
That decision predates the recall-lean finder: with a ≤5-finding gated finder,
one call was both cheap and sufficient. With 10–26 findings the same call is the
bottleneck. The amendment is recorded in that spec with this reasoning, so the
document and the code do not silently diverge.

## 4. Benchmark methodology

### 4.1 Frozen finder output (replay)

The skeptic can only be measured on what the finder produced, and the finder is
stochastic — re-running it per skeptic variant measures the sum of two noise
sources. Prior runs showed exactly this: at n=3, a single lucky finding moved a
PR's median.

`guardian_bench.py` gains a replay mode: finder output per benchmark PR is
recorded once, and every skeptic variant judges the same frozen set. This
isolates the variable and removes the finder's cost from each iteration.

### 4.2 One run yields the whole curve

Benchmarks run at threshold 0 (nothing hidden) and record, per finding, its
`impact_score`, its `verdict`, and whether it matched ground truth. Recall and
noise as functions of the threshold are then computed **offline** from the
recorded scores. The threshold is chosen from data rather than guessed, and no
extra paid run is needed per candidate value.

### 4.3 Gate

The control is not a historical number. Both arms judge the **same frozen
finder output**: arm A is today's batch skeptic, arm B is the per-finding
skeptic, same model in both. Anything that moves is attributable to the
mechanism.

1. **Noise and recall:** arm B's visible non-GT findings per PR must be lower
   than arm A's, with **zero** lost ground-truth matches relative to arm A. The
   sprint spec allows losing one; replay removes the run-to-run variance that
   leniency existed to absorb, so it is tightened here.
2. **Score separation:** pooled across benchmark PRs,
   `median(impact_score | GT-matching) − median(impact_score | non-GT) ≥ 3`
   points on the 0–10 scale. Pooling rather than per-PR because individual PRs
   carry too few GT matches (1–6) for a per-PR median to mean anything. A flat
   distribution means the mechanism failed even if noise happened to fall — this
   is what distinguishes "the skeptic learned to rank" from "the threshold got
   lucky", and it is precisely what the PR #263 pathology would fail.

### 4.4 Phases

- **Phase 1 (cheap):** mechanism on the current codestral skeptic, run as the
  A/B described above — same model, batch versus per-finding. Score separation
  appearing even on the same-family model means the mechanism works on its own,
  independently of the cross-family question.
- **Phase 2 (paid, needs a budget decision):** model matrix — codestral
  (control), gemini (cross-family cloud), local ollama (cross-family, free).
  The prior gemini benchmark run hit the 30 PLN billing cap; phase 2 does not
  start without an explicit go.

Fixture note: pr-141 has empty ground truth (noise probe) and pr-142 is the known
blind class where the finder produces nothing to judge. Skeptic evaluation is
meaningful only where the finder emitted findings — pr-140, pr-143, pr-144 and
pr-122 carry the signal.

## 5. Testing

Pure unit tests, no live LLM, matching the existing guardian test layer:

- `apply_judgements`: `refuted` drops; `uncertain` keeps and discounts ×0.9;
  `None` (unjudged) keeps with no verdict; `impact_score` merged.
- Threshold filter: hides below-threshold findings, keeps them in the record,
  default 0 hides nothing.
- Sanitizer: a finder-emitted `impact_score` is wiped before the skeptic runs.
- `partial` status and `judged/total` accounting.
- Failure isolation: one raising call does not affect the other judgements.
- Concurrency: a fake provider counting concurrent entries never observes more
  than the configured limit.

## 6. Risks

- **N calls multiply cost even at a smaller prompt each.** Mitigated by the
  narrow per-call context (its own hunks, not the full diff) and measured in
  phase 1 before any paid matrix.
- **Narrow context could cause false refutations.** Mitigated by the explicit
  "cannot check outside these hunks ⇒ uncertain" rule; the gate's zero-lost-GT
  condition is what would catch a regression.
- **The rubric is a new prompt surface, and prompt changes have swung this
  system before.** Mitigated by keeping the truth-axis wording byte-identical
  and confining new text to the importance axis.

## 7. Follow-ups (not this spec)

- #247 blind classes (pr-142 cosine masking, float equality in tests).
- #248 long-output robustness and self-consistency union in the runner.
- Call-site graph context for the finder: replace Mermaid topology with the
  calling lines and signatures via `cgis context` (#19). Related to the tooling
  Gemini's review advice pointed at, and worth its own issue.
