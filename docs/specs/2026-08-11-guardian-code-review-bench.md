# Guardian vs Martian Code Review Bench — calibration before comparison (#342)

**Status:** research input, **nothing shipped** (2026-08-11)
**Issue:** #342
**Lane:** guardian

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

**Cost.** Judge calls only, and now counted rather than guessed: **2430 pair
calls per judge**, 4860 for two. Negligible on a flash-tier model.

### Pre-registered gates

Per `guardian_experiment_discipline` — these are written before the run and are
not to be renegotiated after seeing the numbers.

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

**Phase 1 is worth running on its own.** Even if we never touch the 50 PRs, it
tells us whether `benchmarks/guardian/` measures what we think it measures.

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
