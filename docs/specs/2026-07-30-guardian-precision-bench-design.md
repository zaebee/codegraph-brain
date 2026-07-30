# Guardian precision baseline + production finder recordings — design (#279)

**Status:** approved (2026-07-30)
**Issue:** #279
**Lane:** guardian

## Goal

Make Guardian's precision measurable and its production runs replayable offline.
Two deliverables: a benchmark entry that pins the current false-positive rate as
a number, and a recording artifact so any future run can be re-scored without
spending an API call.

This spec does **not** attempt to fix precision. Measuring first is the point.

## Background

`/guardian review` on #278 produced **10 findings**, all skeptic-confirmed
(`skeptic_status: ok`, 10/10), and **none of them real**. One — "remove the
unreachable `raise AssertionError`" — would have broken the build: deleting that
line fails `mypy` with `Missing return statement`. The other nine are catalogued
in #279 with their refutations.

Two gaps made that observation a one-off anecdote rather than a datum:

1. **No benchmark entry covers a precision failure.** The suite measures recall
   against curated defects; nothing pins how much noise the finder emits.
2. **Nothing persists what the finder actually said.** The workflow uploads a
   metrics artifact (token counts) and posts a rendered review. The rendered
   inline comment drops `evidence` and `confidence`
   (`render.py:47`–`56`), so the run cannot be faithfully reconstructed from it
   — which is exactly why this baseline had to be assembled by hand.

## Part 1 — the benchmark entry

`benchmarks/guardian/pr-278.yaml`:

```yaml
pr: 278
base: 2e768cef585f10481970697657b0a9b40a411f47
head: 6ee175e553c784a363c190e00021be5d365fb8dd
findings: []
ambiguous: []
```

`head` is the commit Guardian actually reviewed, per the CURATION.md rule that
`head` = review head rather than the final pull head. The two later commits on
that branch (docs, dependency fix) postdate the review.

With `findings: []`, `score()` yields `recall = 1.0` vacuously, and
`precision = 0 / (0 + noise)` — so the entry measures noise only. On the recorded
run that is **precision 0.0, noise 10**, which becomes the regression number.

### `ambiguous` must stay empty — and that is a policy extension

`match_findings` excludes ambiguous hits from the precision denominator, and it
does so **per file**, not per finding: `prediction.file in ambiguous_files`. One
ambiguous entry on `base.py` would exempt every prediction on `base.py`.

CURATION.md currently routes three kinds of finding to `ambiguous`: style/idiom
nits, review-dialogue resolutions, and suggestions declined with a reason. Our
ten fit none of them. They are not stylistic preferences and not declined
suggestions — they are **factually false claims** (an annotation that exists, an
integer overflow impossible in Python, a "dead" line the type checker requires).

Filing them as ambiguous would make the benchmark structurally unable to observe
a precision failure. They count as noise. CURATION.md gains a fourth rule saying
so explicitly, so the next curator does not "helpfully" reclassify them:

> **Refuted claims → GT `findings` omission, NOT `ambiguous`.** A finding
> disproved by execution (the code cannot behave as claimed) is noise. Ambiguous
> is for judgement calls, not for errors.

## Part 2 — production finder recordings

### Saving the post-skeptic result is safe — the load path already handles it

An earlier draft of this spec proposed capturing the finder's output at a seam
before the skeptic runs, and plumbing it up through `RoutedReview`. Reading the
code closely retired that design: **the codebase already anticipates recordings
made on a run that had a skeptic.**

Three facts, each verified in source:

- `apply_judgements` (`skeptic.py:168`) **annotates**; it never drops a finding.
- `visible_findings` (`skeptic.py:194`) says so explicitly: "Hidden findings stay
  in `ReviewResult.findings` so metrics and the benchmark still see what was cut."
  Refuted findings survive in the result.
- `load_finder_recording` strips the skeptic's marks on read — `verdict`,
  `skeptic_note`, `impact_score`, and the `skeptic_status`/`judged`/`total`
  counters — with a docstring naming this exact case: "A recording captured from
  a run that HAD a skeptic would otherwise smuggle those verdicts into the next
  variant's scoring."

So `run_guardian` can record `routed.result` together with the diff, and a replay
still starts from unjudged findings. No seam, no `RoutedReview` change, nothing
threaded through the review core.

The bench disables the skeptic while recording (`guardian_bench.py:118`) because
paying for a skeptic pass whose output is about to be stripped is waste — not
because a skeptic-run recording would be invalid.

**One documented fidelity gap.** `apply_judgements:189` rewrites `confidence` to
`round(confidence * 0.9)` for `uncertain` verdicts, and the load path does not
restore it. A production recording therefore carries a 10% lower confidence on
that subset. This does not affect what #279 measures: precision and recall are
set-membership, and confidence only orders greedy matching — irrelevant against
an empty ground truth. It is recorded here and in `save_finder_recording`'s
docstring so nobody later reads a recorded confidence as the finder's own number.

### The recording model moves out of `bench.py`

`FinderRecording`, `save_finder_recording`, `load_finder_recording` and
`_validated_recording_path` currently live in `bench.py`. Production code
importing the benchmark module would invert the dependency — bench should depend
on the review path, not the other way round.

They move to a new `src/cgis/guardian/recording.py`, with `bench.py` importing
from it. This follows the precedent already set in this package: `diff_index.py`
exists because the per-file diff split moved out of `chunker.py` the moment the
skeptic pass became a second consumer. Same situation, same resolution.

Nothing about the format or validation changes — the move is mechanical, and the
existing tests for it must pass untouched.

### Surfaces

- `scripts/guardian_review.py` — new `--record-finder PATH`.
- `.github/workflows/guardian.yml` — pass `--record-finder guardian_finder.json`
  and upload it, mirroring the existing metrics artifact step
  (`guardian-finder-${{ env.PR_NUMBER }}`, `retention-days: 7`).

Recording is **unconditional**, not flag-gated behind a repo variable. Today's
baseline is unreproducible precisely because nobody thought to enable a flag
before the interesting run happened; a recording that requires foresight is a
recording that will be missing when it matters. The cost is one JSON per review,
holding findings plus a diff already in memory.

## What this does not give

Recording `routed.result` means the saved JSON **does** retain the skeptic's
verdicts, notes and impact scores — `load_finder_recording` discards them on read
so replay arms stay comparable, but they are on disk. So "the skeptic confirmed
10/10" is recoverable by reading the artifact directly, just not through the
bench replay path.

What is **not** free is re-running the skeptic to see how it would rule on a
changed prompt or model: that is `--replay-finder` with a key, the workflow #246
designed.

What becomes free is the half that matters for #279: **finder precision, scored
without a single API call.**

## Error and edge handling

- Empty diff / no findings: the recording is still written (an empty
  `ReviewResult` is a valid datum — "the finder found nothing here").
- A failed review writes no recording, for the same reason it writes no metrics:
  `run_guardian` has no `try` around the routed call (#275). Recording failures
  is a separate concern and stays out.
- The recording path is CLI-supplied and therefore untrusted; it goes through the
  existing `_validated_recording_path` (moving to `recording.py`), which already
  resolves and suffix-checks. No new validation is invented.
- The upload step mirrors the metrics one exactly, with **no** `if:` condition —
  so like the metrics artifact, a recording exists only for runs that completed.
  That matches the failure semantics already stated above rather than adding a
  second, inconsistent rule.

## Testing

Unit:

- `run_guardian(record_finder=path)` writes a file that `load_finder_recording`
  reads back with identical findings and diff.
- `run_guardian` without `record_finder` writes nothing.
- **Refuted findings survive into the recording.** With a stub skeptic that
  refutes everything, the recorded file still contains every finding, and loading
  it returns them with `verdict is None`. This is the regression test for the
  whole "post-skeptic is safe" argument — if `apply_judgements` ever started
  dropping findings, this fails.
- Round trip: save a synthetic `FinderRecording`, score it against a ground truth
  with `findings: []`, and assert `precision == 0.0` and `noise == len(findings)`
  — the whole path exercised with no provider key.

Benchmark data:

- `pr-278.yaml` loads through `load_ground_truth` without validation errors.

## Acceptance criteria

1. `uv run python -c` scoring a synthetic recording against `pr-278.yaml` reports
   `precision 0.0`, and the run needs no API key.
2. `make format && make lint && make type-check && make pytest && make doc-coverage`
   all pass.
3. A live `/guardian review` produces a `guardian-finder-<pr>` artifact that
   `load_finder_recording` parses. Post-merge; cannot be checked from the branch.

## Out of scope

- **Fixing precision.** No prompt or skeptic change here. Any such change must be
  measured against this baseline, which is the reason the baseline exists.
- Recording skeptic verdicts (see "What this does not give").
- #277 chunk routing — untouched. Recording happens in `run_guardian`, above
  the routing decision, so both paths are covered without either being touched.
