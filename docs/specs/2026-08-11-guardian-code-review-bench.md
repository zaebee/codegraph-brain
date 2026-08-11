# Guardian vs Martian Code Review Bench — calibration before comparison (#342)

**Status:** research input + **Phase 1 results** (2026-08-11)
**Issue:** #342
**Lane:** guardian

> **Update.** Phase 1 has been built, run with two judges, and **audited
> adversarially twice — the audits falsified parts of the first write-up of its
> own results, and then part of the scorer that produced them.** §"Phase 1 —
> results" carries four explicit retractions (a silent population choice that
> decided both verdicts, a gate amended three minutes into the run while this
> document claimed it could not be renegotiated, a gate implemented differently
> from its registration, and a scorer that under-counted the judge's true
> positives). Where results contradict
> an expectation stated earlier in this document, the results win — the earlier
> text is left standing rather than edited into agreement, so the falsification
> stays visible.
>
> **Phase 2 was blocked, and for a different reason than first published:** the
> mechanism (reported precision 1.00 for a review two judges score at
> 0.14–0.19), not the correlation gate, which turned out not to be robust.
> **Unblocked 2026-08-11 by #345**, which made an `ambiguous` hit count as a
> false positive — see §"What this changes" item 2.

## Verdict up front

Reading the benchmark's raw results changed what this work is for. Three things:

1. **We cannot publish a Guardian row yet, and the blocker is our own scorer.**
   Their ground truth is semantic (an LLM judge asking "same underlying issue?"),
   ours is a greedy match over curated entries with a per-file `ambiguous`
   exemption. The two produce different numbers on the same review. Until we know
   the conversion factor, any Guardian figure placed next to their leaderboard is
   a category error. **Phase 1 is the deliverable; Phase 2 is contingent on it.**
2. **80% of the benchmark measures a Guardian that does not exist.** 40 of the
   50 PRs are Java, Go, Ruby and TypeScript.
   `ContextCollector.get_changed_py_files` (`collector.py:105`–`117`) filters on
   `p.endswith(".py")`, so on those PRs Guardian collects no file context and no
   graph context — the finder sees a bare diff. Guardian's differentiator is
   absent from four fifths of the test set. This does not invalidate the exercise;
   it dictates how results must be reported (see "Reporting is stratified, and
   this is not optional", under Phase 2).

   **Halved by #344 (2026-08-11): TypeScript is now collected.** The filter,
   the code fence and the FQN helper all moved behind
   `cgis.extractors.registry`, so the 10 Cal.com PRs join the 10 Python ones —
   **20 of 50 with graph context, not 10**. That doubles the evidence behind
   gate G5 ("graph-enabled slice recall exceeds bare-diff slice recall by ≥10
   points"), which until now rested on 10 PRs. Java, Go and Ruby — 30 PRs —
   remain diff-only, so stratified reporting still is not optional, and the
   claim above should be read as 60%, not 80%.
3. **The field's recall ceiling is ~66%.** No tool on the leaderboard exceeds it.
   Our recall work (#247, #248, #258) has been implicitly calibrated against
   100%. It should be calibrated against 66%.

## The source

[`withmartian/code-review-benchmark`](https://github.com/withmartian/code-review-benchmark),
MIT. Open: the PRs, the golden comments, the judge prompt, the pipeline, the
dashboard, and the raw per-tool results.

Two tracks:

| track | ground truth | freshness |
|---|---|---|
| **offline** | 50 PRs, human-verified golden comments | fixed — contamination risk acknowledged by the authors |
| **online** | which bot comments a developer actually fixed, harvested from GitHub Archive via BigQuery | weekly, no training-set overlap |

This spec concerns the **offline** track only. The online track requires Guardian
to be running as a GitHub App on public repos, which #305 has already decided
against for now.

### The offline corpus

| project | language | PRs |
|---|---|---|
| Sentry | Python | 10 |
| Grafana | Go | 10 |
| Cal.com | TypeScript | 10 |
| Discourse | Ruby | 10 |
| Keycloak | Java | 10 |

Other distributions worth knowing before reading any result:

- **difficulty:** `subtle` 36, `moderate` 10, `very_subtle` 3, `obvious` 1
- **concern:** `correctness` 39, `reliability` 5, `security` 5, `maintainability` 1
- **pr_size:** `medium` 22, `small` 20, `large` 8
- **change_type:** `feature` 32, `performance` 7, `bug_fix` 6, rest ≤3

This is a **subtle-correctness-bug benchmark**, not a broad code-quality one.
Style, idiom and maintainability findings — a large share of what Guardian
currently emits — score as false positives here by construction.

### Ground truth format

`offline/golden_comments/<project>.json` — a list of PRs, each with `pr_title`,
`url` (upstream, not the fork) and `comments`:

```json
{
  "comment": "Race condition: Multiple concurrent requests could pass the device
              count check simultaneously and create devices beyond the limit.",
  "severity": "High",
  "category": "concurrency"
}
```

**No file, no line.** Matching is purely semantic. Guardian's `Finding` carries
`file` and `line`; those fields are simply unused on this benchmark, which
removes one of our failure modes (right bug, wrong anchor) from the measurement.

Severity drives the three profiles. Fixed FP, growing GT:

| profile | GT comments |
|---|---|
| `strict` | 139 |
| `core` | 158 |
| `all` | 173 |

`core` is the dashboard default and the basis for every number below.

### The judge

`step3_judge_comments.py`, one golden × one candidate per call:

> Determine if the candidate identifies the SAME underlying issue as the golden
> comment. Accept semantic matches — different wording is fine if it's the same
> problem. […] Respond with ONLY a JSON object:
> `{"reasoning": ..., "match": true/false, "confidence": 0.0-1.0}`

Judge model is `MARTIAN_MODEL`. Published runs use Claude Opus 4.5, Sonnet 4.5
and GPT-5.2 — deliberately three, to expose judge variance. There is a dedup
pass (`step2_5_dedup_candidates.py`) so a tool that says the same thing five
ways is not credited five times.

## The leaderboard, recomputed from source

Not taken from any blog post. Downloaded
`offline/analysis/benchmark_dashboard.json` and computed from
`overall_metrics`; F₀.₅ derived from the published P and R.

Judge = Claude Opus 4.5, profile = `core`, ordered by F₀.₅:

| # | tool | P | R | F1 | F₀.₅ | TP | FP | FN |
|---|---|---|---|---|---|---|---|---|
| 1 | Qodo Extended | 67.1 | 64.6 | 65.8 | 66.6 | 102 | 50 | 56 |
| 2 | Cubic v2 | 61.5 | 65.8 | 63.6 | 62.3 | 104 | 65 | 54 |
| 3 | Augment | 59.5 | 65.2 | 62.2 | 60.6 | 103 | 70 | 55 |
| 4 | Qodo v2 | 55.4 | 62.0 | 58.5 | 56.6 | 98 | 79 | 60 |
| 5 | Macroscope | 61.1 | 48.7 | 54.2 | 58.1 | 77 | 49 | 81 |
| 6 | GitLab Duo | 53.5 | 52.5 | 53.0 | 53.3 | 83 | 72 | 75 |
| 7 | Devin | 69.1 | 41.1 | 51.6 | 60.8 | 65 | 29 | 93 |
| 8 | Cursor Bugbot | 56.9 | 46.8 | 51.4 | 54.5 | 74 | 56 | 84 |
| 9 | Greptile v4 | 50.9 | 51.3 | 51.1 | 51.0 | 81 | 78 | 77 |
| 10 | **Gemini** | **45.3** | **55.1** | **49.7** | **47.0** | 87 | 105 | 71 |
| 11 | GitHub Copilot | 38.5 | 66.5 | 48.7 | 42.0 | 105 | 168 | 53 |
| 12 | Claude Code (CLI) | 46.3 | 48.1 | 47.2 | 46.6 | 76 | 88 | 82 |
| 13 | Sourcery | 41.0 | 55.1 | 47.0 | 43.2 | 87 | 125 | 71 |
| 14 | Kodus | 59.2 | 38.6 | 46.7 | 53.5 | 61 | 42 | 97 |
| 15 | Baz | 61.5 | 37.3 | 46.5 | 54.4 | 59 | 37 | 99 |
| 16 | CodeRabbit | 32.6 | 59.5 | 42.2 | 35.8 | 94 | 194 | 64 |
| 17 | CodeAnt | 40.1 | 41.1 | 40.6 | 40.3 | 65 | 97 | 93 |
| 18 | Claude Code | 43.2 | 38.0 | 40.4 | 42.0 | 60 | 79 | 98 |
| 19 | KG | 54.3 | 15.8 | 24.5 | 36.5 | 25 | 21 | 133 |
| 20 | Graphite | 100.0 | 7.6 | 14.1 | 29.1 | 12 | 0 | 146 |

The Sonnet-4.5 and GPT-5.2 judges shift absolute values by ~4–8 points but
preserve the ordering of the top and bottom clusters. **Ranking is robust to
judge choice; absolute scores are not.** Any Guardian number must therefore name
its judge.

### What this table settles for us

**Gemini is measured, and its profile is exactly the one we diagnosed.**
45.3 precision at 55.1 recall — 105 false positives against 87 true positives.
Roughly average on this field. The recall memo's conclusion (the finder's
recall problem is a *prompt* problem, not a model-capability problem) gains an
independent data point: the same model family, prompted by Google, reaches
55% recall. Our `0/5` was not the model's ceiling.

**Graphite is the named extreme of our failure mode.** 100% precision, 7.6%
recall, 12 findings across 50 PRs. This is what "favor precision over recall"
converges to when taken to its conclusion — the framing #258 already flagged as
incompatible with our needs. Worth citing by name in the finder prompt review.

**The precision-first cluster loses on every F-measure.** Devin (69.1 P) sits
7th; Baz and Kodus (~60 P) sit 14–15th. Nothing above rank 4 has recall below
62. On this benchmark recall is where the score lives — which is an argument
about *this* benchmark's construction (36 of 50 PRs are `subtle`), not a
universal law. Stated so we do not over-generalise it.

**Claude Code as an interactive CLI reviewer scores 46.3 / 48.1 (rank 12).**
Relevant because it is the closest published analogue to how `/guardian review`
is invoked, and because published claims that it leads this benchmark on F1 are
not supported by the current data.

## Where Guardian stands today, in our own units

From `benchmarks/guardian/results.jsonl`, most recent configuration
(`mistral-medium-latest`, `guardian_sha c4c391b`, all from this repo):

| PR | recall | precision | noise |
|---|---|---|---|
| 143 | 1.00 | 0.286 | 15 |
| 144 | 0.60 | 0.375 | 5 |
| 140 | 0.20 | 0.214 | 11 |

**These are not comparable to the table above** and must not be quoted as if
they were. Different corpus (our repo vs. 5 large OSS projects), different
ground truth (our curation vs. theirs), different matcher (greedy entry vs. LLM
judge), different ambiguity policy (we exempt whole files; they have no such
concept). Establishing the size of that gap is the entire point of Phase 1.

## Phase 1 — calibrate our scorer (the actual deliverable)

**Question.** Given the same review output and the same PR, how far apart are
`bench.score()` and the Martian judge, and in which direction?

**Method.**

1. Take the recorded finder outputs from `benchmarks/guardian/results.jsonl`.
   No new LLM review calls — this phase re-scores existing runs.
2. Convert each `benchmarks/guardian/pr-*.yaml` ground truth into the Martian
   golden-comment shape. Our entries already carry prose descriptions, and
   `severity` is already a required field on every entry.
3. Run `step3_judge_comments.py`'s prompt over the (golden × candidate) matrix
   with two judges, so judge variance is visible from the start.
4. Report both scores side by side per PR, plus the `ambiguous` delta: our
   precision with the per-file exemption vs. without it.

### What the corpus actually holds — measured, not estimated

`benchmarks/guardian/` carries **8 curated PRs**, not the 9 an earlier draft of
this document claimed. Of those, **6 have recorded reviews** in
`results.jsonl` — pr-278 and pr-313 have fixtures but no recorded run — giving
**67 scored reviews** across finder models, feature flags and bench arms.

The unit of correlation therefore has two candidates, and they are not
equivalent:

| unit | n | note |
|---|---|---|
| per review (one recorded run) | **67** | primary — this is the spread G1 needs |
| per PR (runs averaged) | **6** | conservative, but 6 points barely supports a rank correlation |

**The per-run statistic is primary and the per-PR one is reported alongside it.**
Runs on the same PR share ground truth, so per-run points are not fully
independent and a high ρ could partly reflect PR-level clustering; the per-PR
figure is the check on that. Reporting only one of the two would be a choice
made after seeing which looked better, which is exactly what pre-registration
exists to prevent. Note also that pr-141 has **no ground-truth findings at all**,
so it contributes a constant precision column — Spearman is undefined on
constants, and the calibration reports that as `undefined`, never as 0.

> **Corrected after the fact (see R1/R2 in the results).** Two things above are
> wrong. The n = 67 is a *non-empty-review* count presented as if it were the
> corpus: 118 scored reviews exist. And pr-141's as-reported precision column is
> not constant — it is {0.0, 1.0}, because two of its runs put every candidate
> on an ambiguous file and scored the vacuous 1.0. Its *strict* and *judge*
> columns are the constant ones. Left standing rather than edited, per the
> falsification-stays-visible rule.

**Cost.** Judge calls only, and now counted rather than guessed: **2430 pair
calls per judge**, 4860 for two. Negligible on a flash-tier model.

### Pre-registered gates

Per `guardian_experiment_discipline` — these are written before the run and are
not to be renegotiated after seeing the numbers.

> **This section was amended at 17:14:26, three minutes after the first judge
> record was written at 17:11:39.** G1's primary unit was changed from per-PR to
> per-run. The amendment made the gate harder, and its motivation (a corpus
> recount) was genuine, but it happened after collection began and is disclosed
> here rather than left for a reader to reconstruct from git. Full chronology in
> R2 below. G2's registered wording ("relative to the judge") also does not match
> what was implemented — R3.

- **G1 (legibility).** Precision from the two scorers correlates at Spearman
  ρ ≥ 0.6 over the 67 recorded reviews, with the 6-point per-PR figure reported
  beside it. *If G1 fails, the two scorers are measuring different things and
  Phase 2 is cancelled* — we would instead have found that our benchmark's
  notion of a "hit" is idiosyncratic, which is a more important result than a
  leaderboard row.
- **G2 (ambiguous policy).** The per-file `ambiguous` exemption inflates our
  precision by ≤ 10 percentage points relative to the judge. *If G2 fails*, the exemption
  is doing more work than intended and `CURATION.md` needs revision before any
  external comparison — see the §"ambiguous must stay empty" argument in
  `2026-07-30-guardian-precision-bench-design.md`, which already anticipated
  this being load-bearing.
- **G3 (judge variance).** The two judges agree on ≥ 80% of individual
  match/no-match decisions. *If G3 fails*, single-judge numbers are not
  reportable and everything downstream needs a three-judge median, as Martian
  themselves do.
  > **This gate was unfalsifiable as written, and that was not noticed until the
  > audit.** A judge calls ~5% of pairs a match, so two judges agree by chance
  > ~91% of the time — above the threshold before either has said anything. The
  > result is reported against the registered form anyway, with κ beside it.

**Phase 1 is worth running on its own.** Even if we never touch the 50 PRs, it
tells us whether `benchmarks/guardian/` measures what we think it measures.

## Phase 1 — results (2026-08-11)

Ran, then audited adversarially — twice. **The first audit found three defects
in this section's first version, two of them in its honesty rather than its
code; the second found a fourth in the scorer itself.** What follows is the
corrected version; the retractions are stated rather than quietly edited,
because a document arguing for pre-registration discipline cannot fix its own
record silently.

Data: **118 scored reviews × 2 judges** (`gemini-2.5-flash`,
`mistral-medium-latest`), 4860 pair calls. Gemini: 0 failed pairs. Mistral: 9 of
2430 (0.4%), on 7 rows, flagged in the report. Records committed at
`benchmarks/guardian/calibration.jsonl`; reproduce with
`uv run python scripts/guardian_calibrate.py report`, and check that the
committed records still imply the published numbers with
`uv run python scripts/guardian_calibrate.py rescore` (expected: 0 changes).

### Four retractions, up front

**R1 — the first version silently dropped 51 of 118 reviews, and that choice
decided both verdicts.** `load_rows` required a non-empty `findings` list, so
every review that found nothing was excluded. Those are real, non-`parse_failed`
runs with real scores. Both gates flip on the full population:

| | ALL reviews (n=118) | NON-EMPTY (n=63) |
|---|---|---|
| G1 per-run ρ, gemini | **+0.722 PASS** | **+0.422 FAIL** |
| G1 per-run ρ, mistral | **+0.714 PASS** | **+0.349 FAIL** |
| G2 mean inflation | **+8.7 pp PASS** | **+16.2 pp FAIL** |

Neither population was pre-registered. Both are defensible: empty reviews are
real measurements, *and* they agree by shared vacuous convention (precision 1.0
on both sides by definition, not by measurement), which manufactures
correlation. The honest statement is that **Phase 1 measured agreement
conditional on a non-empty review, and did not say so.** The runner now collects
both and the report prints both; empty reviews cost zero judge calls, so there
was never a reason to exclude them at collection time.

Two consequences for numbers published earlier in this document's history: the
non-empty population is now defined as "the judge saw at least one candidate"
(`n_candidates > 0`) rather than "the row had findings" — four reviews had
findings that the skeptic refuted, so the judge saw nothing. **ρ = +0.422 at
n = 63 supersedes the +0.479 at n = 67 first reported.**

**R2 — the gate that failed is not the gate that was registered, and this
document claimed otherwise.** Chronology from git and the run log:

| time | event |
|---|---|
| 16:55:50 | G1 registered as **"per-PR precision, ρ ≥ 0.6"** (`5b4603c`) |
| 17:11:39 | first judge record written |
| **17:14:26** | G1 rewritten to make **per-run** primary (`0276f33`) — three minutes into the run |
| 17:40:13 | last judge record |
| 17:55:15 | results committed |

Under the gate as originally registered, per-PR ρ = +0.657 → **G1 would have
passed**. The amendment made the gate harder and the verdict more conservative,
and its stated motivation (the corpus is 8 PRs and 67 runs, not 9 PRs) was
genuine. It is still an amendment made after data collection began, and the
sentence this document used to carry — *"not renegotiable by pointing at the
strict column: the gate was written on the reported number precisely so that
this could not be argued away after the fact"* — **is retracted.** The gate was
renegotiated. Disclosure was owed and not given.

The per-PR statistic that would have passed is in any case not trustworthy: at
n = 6 it moved from +0.657 to **+0.257** merely by correcting the population,
and ρ = 0.657 at n = 6 carries p ≈ 0.09. It is not a check on anything and is no
longer described as one.

**R3 — G2 as implemented never consulted the judge.** Registered: "inflates our
precision by ≤ 10 pp *relative to the judge*". Implemented: `our_precision −
our_precision_strict`, a self-ablation computable from `results.jsonl` alone.
The proxy is defensible — strict precision tracks the judge at mean |Δ| ≈ 0.10 —
but it is not what was registered, and the judge run was not needed to score it.

**R4 — the scorer under-counted the judge, and the bias pointed at our own
conclusion.** `assign_matches` enforced the 1:1 constraint greedily, taking
pairs in descending judge confidence. Greedy is not maximum: a golden whose only
candidate has already been spent by a stronger pair is stranded, and its true
positive is lost. Measured over the committed decision grids, **3 of 236 records
under-reported tp by one** (pr-140 under both judges, pr-144 under gemini).

The direction matters more than the size. Under-counting the judge's true
positives depresses the judge's precision and recall, which **widens the
ours-versus-theirs gap this section publishes as its finding.** A scoring error
that flatters the argument is the kind that survives review, and this one did:
it was written up with a justification ("it mirrors `match_findings`, which is
also greedy") that is symmetry in the error, not absence of one.

Corrected to a maximum bipartite matching. Every affected number is restated
below; **no gate verdict changes** — the shifts are in the third decimal:

| | before | after |
|---|---|---|
| G1 per-run ρ, gemini (ALL / non-empty) | +0.723 / +0.423 | **+0.722 / +0.422** |
| G1 per-run ρ, mistral (ALL / non-empty) | +0.714 / +0.349 | **+0.714 / +0.348** |
| per-run recall ρ, gemini (non-empty) | +0.859 | **+0.863** |
| G2 mean inflation | +8.7 / +16.2 pp | unchanged |
| G3 (raw, κ, positive overlap) | — | unchanged — the decision grids were never in doubt |

Two things this bought beyond the correction. The maximum matching's *size* does
not depend on judge confidence, so a record's score now follows from its stored
decision grid alone — the committed JSONL reproduces its own published numbers,
which it previously could not, because confidences were never stored.
`scripts/guardian_calibrate.py rescore` is that check, and it re-derived these
corrections from the raw rulings already on disk rather than spending the judge
budget again.

One number in the §G2 table below was also simply mistyped in the first version,
unrelated to any of this: pr-122's mistral judge precision is **0.51**, not the
0.58 that was copied from gemini's column.

### G1 — FAIL on the non-empty population, PASS on the full one

Both judges agree closely on every statistic, and **the verdict is decided by
the population choice, not by judge variance**:

| statistic (non-empty, n=63) | gemini | mistral |
|---|---|---|
| per-run, precision **as the bench reports it** | +0.422 | +0.348 |
| per-run, precision **without** the `ambiguous` exemption | +0.756 | +0.723 |
| per-run recall | +0.863 | +0.874 |
| mean \|ours − judge\| | 0.251 | 0.258 |
| mean \|strict − judge\| | 0.099 | 0.096 |

Removing the exemption is the single largest improvement available, on both
judges. But the first version's claim — *"the divergence is almost entirely our
ambiguity policy, not line-anchoring versus semantic matching"* — **overstated
what the data shows**, and the audit's counter-evidence is decisive. Splitting
by whether a PR carries `ambiguous` entries at all:

| slice | n | mean \|strict − judge\|, gemini | mistral | exact matches |
|---|---|---|---|---|
| PRs **without** `ambiguous` entries | 40 | **0.145** | **0.149** | 24/40, 26/40 |
| PRs **with** them, exemption removed | 23 | **0.018** | **0.004** | 21/23, 22/23 |

Agreement is **worst on the slice where the exemption cannot fire**. If ambiguity
policy were the whole story, that slice should agree best. A residual gap of
~0.147 survives on exactly the PRs where ambiguity cannot explain it — plausibly
the line-anchoring-versus-semantic difference this document had declared
exonerated. Supported claim: **the exemption is the largest single contributor,
and a residual disagreement of ~0.15 mean absolute precision remains without
it.**

Note also that the clean/dirty split was chosen after seeing results. It is a
diagnosis, not a test, and on 6 clusters it cannot be more than that.

One further correction: **recall's ρ = 0.86 is partly definitional.** pr-141 has
no ground-truth findings, so its rows score recall (1.0, 1.0) on both sides
automatically. Without it: **+0.801 (gemini), +0.817 (mistral)** — still the
strongest agreement in the table, but not 0.86.

### G2 — FAIL on the non-empty population (+16.2 pp), PASS on the full one (+8.7 pp)

Maximum inflation is **+100 pp under every population and both judges**, and that
is the finding that survives everything else. pr-142 is the mechanism in one
line:

| PR | `ambiguous` entries | P ours | P strict | P judge (gemini / mistral) |
|---|---|---|---|---|
| pr-122 | 0 | 0.66 | 0.66 | 0.58 / 0.51 |
| pr-140 | 0 | 0.27 | 0.27 | 0.40 / 0.40 |
| pr-143 | 0 | 0.09 | 0.09 | 0.12 / 0.12 |
| pr-141 | 2 | **0.29** | 0.00 | 0.00 / 0.00 |
| pr-142 | 1 | **1.00** | 0.14 | 0.19 / 0.14 |
| pr-144 | 4 | **0.29** | 0.05 | 0.04 / 0.04 |

(non-empty population, run-averaged)

Every candidate on pr-142 landed on a file carrying an ambiguous entry, the
precision denominator emptied, and `score()` returned the vacuous **1.0** it
reserves for "nothing wrong was said". Two independent judges scored the same
reviews at 0.14 and 0.19. **The benchmark reported perfect precision for a review
that was almost entirely wrong.** pr-144 is the same shape: its ambiguous entries
cover `drift.py` and `triads.py`, the files carrying 3 of its 5 ground-truth
findings, so the exemption shields nearly every miss on the PR's main file.

This is precisely the mechanism `2026-07-30-guardian-precision-bench-design.md`
predicted in its §"`ambiguous` must stay empty" — the exemption is per **file**,
not per finding. That document argued it from the code and added a curation rule
to contain it. The measurement shows containment failed: three of six PRs carry
ambiguous entries, and on those the reported precision does not describe the
review.

### G3 — passes as registered, and the registered form is uninformative

| | value |
|---|---|
| raw agreement | **97.9%** — PASS against the registered 80% |
| agreement expected by chance | **91.1%** |
| match rates | 4.8% (gemini) vs 4.5% (mistral) |
| **Cohen's κ** | **+0.768** |
| positive overlap (Jaccard on matched pairs) | **63.8%** (88 pairs matched by both) |

G3 was registered as raw per-decision agreement ≥ 80%. **At this base rate that
gate cannot fail**: a judge calls ~5% of pairs a match, so two judges agree by
chance about 91% of the time — above the threshold before either has said
anything. The registered verdict is reported as registered and then discounted,
rather than swapped for a friendlier statistic after the fact; the code prints
both with that explanation attached.

The chance-corrected number is genuinely reassuring: **κ = +0.768** is
substantial agreement, and every ρ above moves by less than 0.08 between the two
judges. Single-judge numbers are reportable. The caveat is the positive class:
**36% of matches are judge-specific** — the judges agree overwhelmingly on what
is *not* a match and only two-thirds agree on what is. Any Phase 2 figure needs
more than one judge, as Martian themselves do.

### What this changes

1. **Phase 2 stays blocked — but on the mechanism, not the gate arithmetic.**
   The ρ-based FAIL is not robust: it depends on an unregistered population
   choice (R1) and rests on a mid-run amendment (R2). What *is* robust across
   both populations and both judges is that our benchmark reports precision 1.00
   for a review two judges score at 0.14–0.19, with a maximum inflation of
   +100 pp. That is sufficient reason not to publish a Guardian number against
   an external leaderboard, and it is the reason of record.

   **Lifted by #345** — the mechanism is gone, so the reason of record no longer
   holds. Note what this does *not* clear: the ρ-based FAIL was never robust and
   is not now retroactively a PASS, and the stratification requirement under
   Phase 2 (80% of the corpus is a language Guardian collects no context for)
   stands untouched.
2. **The remedy is a curation policy decision, and it belongs to `CURATION.md`.**
   Options: drop the exemption; make it per-finding rather than per-file; or keep
   it and stop calling the exempted number precision. They differ in what they
   claim about debatable findings. Filed separately.

   **Resolved 2026-08-11 (#345): the exemption is dropped.** An `ambiguous` hit
   now counts as a false positive; `ambiguous_hits` survives as curation
   diagnostics on both `MatchResult` and `BenchScore`, reported apart from
   `noise` but inside the precision denominator. Rationale and the cost —
   pr-144's three declined-but-defensible clip suggestions now count against
   precision — are recorded in `benchmarks/guardian/CURATION.md`.

   The argument that settled it is not in the options list above. CURATION.md
   routes style nits to `ambiguous` to stop them depressing **recall**, but
   omitting them from `findings` already does that, since recall divides by
   `len(findings)`. The precision exemption was a side effect of the same
   mechanism that was never separately argued — and it runs backwards, because
   guardian's own precision rules forbid style nits, so emitting one *is* a
   precision failure and the exemption hid exactly the failure those rules
   exist to catch.

   **This closes the gap the acceptance criteria asked about without a re-run.**
   Reported precision is now by definition the "strict, no ambig" column above:
   ρ **+0.921 / +0.922** against the two judges over all 118 reviews (was
   +0.722 / +0.714), **+0.756 / +0.723** on the non-empty population (was
   +0.422 / +0.348), mean absolute difference **0.099 / 0.096** (was
   0.251 / 0.258). What remains is the ~0.15 residual of §G1 — a separate
   finding, and the honest error bar on any Phase 2 number.

   `results.jsonl` is deliberately **not** rescored: each row's `precision` was
   correct under the policy in force when it was written, and `matched`,
   `noise` and `ambiguous_hits` are all recorded, so either definition can be
   re-derived. That is the opposite call from `calibration.jsonl` in R4, and for
   the opposite reason — that one was wrong under its own stated algorithm.
3. **The good news, restated at its real size.** Strict precision correlates at
   +0.72 to +0.76 and matches the judge exactly on 45–48 of 63 runs; recall
   correlates at +0.80 to +0.82 once the definitional rows are removed. Once the
   exemption is resolved, the two scorers are close enough for a Phase 2 number
   to mean something — with the ~0.15 residual of §G1 as the honest error bar,
   not zero.
4. **Two method fixes carry into Phase 2.** Report the population explicitly and
   pre-register it. Use κ or positive-class overlap for any agreement gate, never
   raw agreement on a rare positive class.

### An operational finding, paid for in lost data

The first Mistral judge run lost **76.5% of its pairs (342 of 447) to HTTP 429**
and was discarded rather than reported. `BaseProvider._retry` retries only httpx
transport errors — deliberately the set the Mistral SDK itself retries — so a
rate limit raised as an SDK error fails on the first attempt. Right for a review,
which makes a handful of large calls; wrong for a calibration issuing thousands
of tiny ones, where 429 is the expected response rather than an anomaly.

Fixed inside `calibrate.judge_pair`, not in the provider, so that a measurement
task does not silently change how production reviews retry. Loss fell to **0.4%
(9 of 2430)** at concurrency 1 with backoff. **Phase 2 inherits this**: 50 finder
calls plus ~1600 judge calls, and the run must report its failure rate rather
than quietly scoring fewer pairs.

### Threats to validity that remain unaddressed

- **The rendering is not Martian's.** Our goldens are `summary` alone — 5–16
  words, mean ~10 — against Martian's 1–3-sentence golden comments. Worse for
  the anchor-free rationale, our summaries are identifier-dense
  (`_ideal_layer never validates…`), and a function name is positional
  information in prose. A judge matching on shared identifiers behaves more like
  a string matcher — that is, more like `match_findings` — which would **inflate**
  the agreement reported here relative to Martian's actual goldens. Direction is
  inference; the length asymmetry is measured.
- **Neither judge is one of Martian's.** Their published numbers use Opus 4.5,
  Sonnet 4.5 and GPT-5.2, which spread 4–8 points between them. Nothing here is
  on their scale in the judge dimension either.
- **Six clusters is few.** A cluster bootstrap over PRs puts the as-reported ρ
  95% CI at roughly [+0.27, +0.71] — spanning the gate. The design cannot
  statistically separate PASS from FAIL at this cluster count, which is a further
  reason the mechanism, not the correlation, carries the decision.
- **Rank correlation is the wrong family** for "are these on the same scale": a
  scorer reporting judge + 0.3 everywhere would score ρ = 1.0. The agreement
  statistics quoted above (mean |Δ|, exact-match counts) are the more
  informative numbers, and Phase 2 should register a Bland-Altman-style bias and
  limits instead.
- **Seven mistral rows carry unruled pairs** whose tp is therefore biased low.
  `done_keys` treats a row as complete regardless, so a resume never re-judges
  them. Small (9 of 2430 cells) but structural; the report now warns.

## Phase 2 — measure (contingent on G1)

**Method.** Guardian is a CLI over a diff, so the fork-and-install-a-bot path in
the upstream README does not apply. The golden comments carry upstream PR URLs;
`gh pr diff` reproduces the reviewed state directly. No GitHub org, no App
installation, no dependency on #305.

Run `guardian review` over the 50 PRs, judge with their pipeline, report
alongside the leaderboard.

### Reporting is stratified, and this is not optional

Because of the `.py` filter in `collector.py:117`, Guardian is a different system
on different slices of this corpus:

| slice | PRs | Guardian's actual capability |
|---|---|---|
| **Python** (Sentry) | 10 | full — file context + graph context + drift |
| **TypeScript** (Cal.com) | 10 | extractor exists (`typescript_extractor.py`) but the collector's `.py` filter blocks it — **currently diff-only** |
| Go / Ruby / Java | 30 | diff-only, permanently, until those extractors exist |

**The headline number is the Python slice.** The full-50 number is reported too,
labelled as a diff-only baseline, because it is the only figure directly
comparable to the leaderboard — and because the gap between the two slices *is*
the measurement of what graph context buys. That comparison is more interesting
to this project than the rank itself.

The TypeScript slice is the cheap win: covering `.ts`/`.tsx` in the collector
makes 20 of 50 PRs graph-enabled instead of 10 and doubles the evidence behind
G5. Filed as **#344** rather than smuggled into this work — and it is not the
one-line change it looks like: the collector imports the *Python*
`file_path_to_module_fqn`, which on a `.ts` path yields an FQN matching no node
the TS extractor ever emitted, and fails silently. Lifting the suffix filter
alone would produce a Guardian that appears to have graph context and does not.

### Pre-registered gates

- **G4 (floor).** Guardian on the Python slice is not last. Concretely: F₀.₅
  above Graphite's 29.1. A precision-only reviewer that finds nothing is the bar
  to clear, and we have shipped reviews that would not clear it.
- **G5 (the graph claim).** Python-slice recall exceeds full-corpus recall by
  ≥ 10 percentage points. *This is the falsifiable form of "graph context
  helps."* If it
  fails, graph context does not measurably help find bugs on real PRs, and that
  finding outranks the leaderboard position in importance — it goes straight to
  #220 and the context-collector lane.
- **G6 (honest reporting).** Every published Guardian number names its judge
  model, its profile, and its slice. A number without all three is not published.

**Cost estimate.** 50 finder+skeptic runs on PRs substantially larger than ours
(our current runs sit at 7k–27k prompt tokens; real OSS PRs will run higher),
plus ~1600 judge calls. Order $20–60 depending on finder model — dominated by
the finder, not the judge. Cheap enough not to need a budget debate; expensive
enough that G1 must gate it.

## What we take even if both phases are cancelled

1. **β becomes an explicit parameter.** Their dashboard exposes F-β with a
   default of β = 2.0 (recall-weighted); commentary elsewhere quotes F₀.₅
   (precision-weighted). Same data, opposite conclusions about who leads. We
   currently track precision and recall as two independent numbers and let each
   experiment argue informally about the tradeoff. Adopting one stated β per gate
   — chosen per issue, written down before the run — removes an argument we keep
   re-having.
2. **Their `concern` dimension is our axes.** `correctness` / `reliability` /
   `security` / `maintainability` is nearly the axis set behind
   `GUARDIAN_FEATURES=axes` (#333, #334). Their per-dimension slicing gives a
   ready-made way to test whether per-axis fan-out actually raises recall *on its
   own axis*, which our current bench cannot show.
3. **The ~66% ceiling, as a planning input.** Recall gates in #247/#248 should be
   set relative to the achievable frontier, not to perfection.
4. **`severity` on ground truth entries.** Their three profiles fall out of one
   severity field on each golden comment. Our `pr-*.yaml` entries have no
   severity, so we cannot ask "how do we do on the findings that actually
   matter?" Adding the field is a small change with a large return, and Phase 1
   requires it anyway.

## Open questions

- ~~Do the recorded finder outputs cover all the benchmark PRs?~~ **Answered
  while building the runner, and better than expected.** The recordings are not
  needed: `results.jsonl` itself stores every finding's full text alongside the
  score, so all 67 reviews replay from the log. Two facts fell out of checking
  it. The recorded `findings` list includes findings the skeptic *hid*, while
  the scorer ran on `visible_findings(...)`; at threshold 0 the only rule that
  can fire is `verdict == "refuted"`, and reconstructing on that basis matches
  `len(matched) + noise + len(ambiguous_hits)` exactly on all 67 rows. Coverage
  is 6 of the 8 fixtures — pr-278 and pr-313 have no recorded run, so the
  precision-baseline PR the whole exercise was motivated by is, for now, outside
  the calibration.
- Is `mistral-medium-latest` the configuration we want measured, or should
  Phase 2 wait for the recall-lean finder prompt from #258/#248? Measuring the
  current configuration produces a baseline; measuring the next one produces a
  headline. Baseline first is the defensible order, but it costs a second run.
- Contamination: the 50 PRs are public and predate current model cutoffs.
  Martian acknowledge this for the offline track and built the online track in
  response. Our number inherits the same caveat and must state it.
