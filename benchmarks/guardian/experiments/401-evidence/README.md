# Does checker output change the skeptic's verdicts? (#401)

#402 gave the skeptic the repository's `mypy --strict` and `ruff check` output. Whether
that changes anything was unmeasured, and `GUARDIAN_EVIDENCE=1` was switched on without a
number behind it. This is the number.

## Design

One review, three arms, paired at the level of the individual finding.

| arm | what it is |
|---|---|
| **baseline** | the verdicts the recorded review actually produced, no evidence |
| **control** | the same 24 findings re-judged, still no evidence |
| **treatment** | the same 24 findings re-judged with evidence, collected at the reviewed commit |

The finder is never called. The subject is `guardian-finder-399`, the review that motivated
the RFC: 24 findings, of which the RFC classified exactly one as substantive.

**The control arm is not optional rigour.** `gemini.py` sends no temperature and
`guardian.yml` sets none, so the skeptic runs at the provider default: the recorded verdicts
are one draw, not a fixed point. Baseline against treatment therefore measures the effect
*plus* the resampling noise, and only a no-evidence replay separates them. This is the same
confound the replay arm removes at the finder, reappearing one level down.

Evidence is collected in a worktree at `05af753d` — the commit `refs/pull/399/head` pointed
at when the review ran. **Not** the artifact's `workflow_run.head_sha`: `guardian.yml` is
triggered by `issue_comment`, so that field is the tip of the trunk (`fd4e53a2`, a release
commit) and the reviewed files do not exist in it.

## Results

Baseline: **19 confirmed / 5 refuted**.

| arm | conf→conf | conf→ref | conf→unc | ref→conf | ref→ref | moved | cites a checker |
|---|---:|---:|---:|---:|---:|---:|---:|
| control | 17 | 2 | 0 | 2 | 3 | 4 | 6/24 |
| treatment, draw 1 | 11 | 8 | 0 | 0 | 5 | 8 | 7/24 |
| treatment, draw 2 | 11 | 7 | 1 | 1 | 4 | 9 | 8/24 |

Draw 2 carries the per-finding rows (`pr-399-treatment-rows.jsonl`); draw 1 predates that
output and survives only as the aggregate above.

### The mechanism works exactly where it was aimed

Six findings claimed a mypy rule the repository does not have. **All six were refuted, and
all six cite the checker output.** The rationale on finding 0 is the RFC's hypothesis
verbatim:

> The project's mypy --strict checker, run as part of the review process, reports 'Success:
> no issues found', directly contradicting the claim that the use of `Any` is prohibited in
> strict mypy mode for this code.

That answers open question 1. The failure was **capability, not disposition**: shown the
output, the skeptic withdraws the claim; not shown it, it confirms.

### The one true finding was withdrawn

Finding 4 — `git diff` can fail in a way other than the promised `MissingFixtureError`, the
finding that produced the actual fix in #399 — was refuted, **without citing any checker**:

> the code explicitly checks the validity of `base` and `head` SHAs using `git rev-parse`
> before the `git diff` call … thus the specific failure mode is already handled

which reproduces the half of the argument that `diff_for`'s own comment rejects: *"unlikely"
is not the same as "cannot"*.

| | baseline | treatment |
|---|---:|---:|
| false findings confirmed | 18 | 12 |
| true findings confirmed | 1 | **0** |

### What the control arm bought

The loss of finding 4 is **not attributable to the evidence**: no checker is cited, and the
control arm produced two `confirmed → refuted` flips of its own out of 24 with no evidence
present. Without the control, the honest-looking conclusion would have been "evidence
destroys recall", and it would not have been supported.

Two draws of the same treatment arm also differ (8 vs 7 refutations, 0 vs 1 restoration), so
single-draw asymmetries here are not results.

## What this does not establish

- **The recall denominator is one.** One review, one substantive finding. "Recall went to
  zero" is literally true and statistically nearly weightless.
- **Attribution by rationale text is a weak proxy.** Citations barely moved between arms
  (6/24 control vs 7–8/24 treatment) while verdicts moved a great deal.
- **The control has no per-finding rows.** Whether finding 4 also dies without evidence is
  the single most informative unmeasured cell, and it costs 24 skeptic calls.
- **Six of 24 control rationales cite mypy or ruff although no checker output was supplied.**
  The skeptic asserts what a checker would say — the same unverified move the RFC faults the
  finder for. Unregistered; worth its own issue.

## Reproducing

```bash
scripts/guardian_replay_skeptic.py --recording pr-399-recording.json --control
scripts/guardian_replay_skeptic.py --recording pr-399-recording.json \
    --at 05af753d8895eab00609e7e58d89aa72ebd1777d --out rows.jsonl
```

Requires `GUARDIAN_SKEPTIC=gemini`, `GUARDIAN_SKEPTIC_MODEL=gemini-2.5-flash` and the
matching key. 24 skeptic calls per arm, no finder calls. The recording is committed here
because the GitHub artifact it came from expires after seven days.
