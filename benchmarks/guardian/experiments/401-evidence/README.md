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
| control, draw 1 | 17 | 2 | 0 | 2 | 3 | 4 | 6/24 |
| control, draw 2 | 18 | 1 | 0 | 0 | 5 | 1 | 6/24 |
| treatment, draw 1 | 11 | 8 | 0 | 0 | 5 | 8 | 7/24 |
| treatment, draw 2 | 11 | 7 | 1 | 1 | 4 | 9 | 8/24 |

Draw 2 of each arm carries the per-finding rows (`pr-399-control-rows.jsonl`,
`pr-399-treatment-rows.jsonl`); the first draws predate that output and survive only as the
aggregates above.

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

### What the control arm bought, and how it reversed the first reading

The aggregate alone suggested the loss of finding 4 was noise: no checker was cited on it,
and control draw 1 produced two `confirmed → refuted` flips of its own. **The per-finding
rows say otherwise.** In the control draw that carries rows, finding 4 was *confirmed*, and
the rationale is correct:

> The `subprocess.run` call for `git diff` includes `check=True`, but the
> `CalledProcessError` it raises on command failure is not explicitly caught, leading to a
> different error type than `MissingFixtureError` …

That is the defect, stated accurately, by the same model that withdrew it once evidence was
in the prompt. The single control flip was finding 6, not finding 4.

So the loss is **on the evidence arm's side of the ledger**, and the earlier "not
attributable" reading — published in #406 and in the #401 comment before these rows existed —
was wrong. It rested on an aggregate, which is exactly the shape of number that cannot
answer *which*.

The two arms separate cleanly per finding:

| class | control | treatment |
|---|---|---|
| 6 false mypy claims | all confirmed | all refuted, all citing the checker |
| the 1 true finding | **confirmed, correctly reasoned** | **refuted**, citing no checker |

The mechanism on the false claims is visible in the control's own words: it confirmed
finding 0 on a conditional it could not check — *"**if** `mypy --strict` is configured to
disallow `Any` explicitly, it would be flagged"*. Evidence turns that hypothetical into a
reading, which is precisely the RFC's design intent.

Why the true finding falls is not explained by the same mechanism, because its refutation
cites nothing. The unattributed moves are 3 in treatment against 1 in control, which is
consistent with the evidence section shifting the skeptic toward refutation generally rather
than only where the output bears — but at these counts that is a hypothesis, not a result.

Two draws of the same arm differ in both arms (control 4 vs 1 flips, treatment 8 vs 9), so
single-draw asymmetries are not results.

## What this does not establish

- **The recall denominator is one.** One review, one substantive finding. "Recall went to
  zero" is literally true and statistically nearly weightless.
- **Attribution by rationale text is a weak proxy.** Citations barely moved between arms
  (6/24 control vs 7–8/24 treatment) while verdicts moved a great deal — and the rows show
  why: the control cites a checker on the *same six findings*, to confirm them.
- **Only one draw per arm has rows.** Whether finding 4 survives the control reliably or was
  lucky in this draw is unmeasured; control draw 1 had two unidentified `confirmed → refuted`
  flips.
- **Six of 24 control rationales cite mypy or ruff although no checker output was supplied**
  (#407). Not merely fabricated: fabricated *in support of the wrong verdict*. The same six
  findings carry a checker citation in both arms, confirmed in one and refuted in the other,
  and only one of the two had a checker.

## Reproducing

```bash
scripts/guardian_replay_skeptic.py --recording pr-399-recording.json \
    --control --out control-rows.jsonl
scripts/guardian_replay_skeptic.py --recording pr-399-recording.json \
    --at 05af753d8895eab00609e7e58d89aa72ebd1777d --out treatment-rows.jsonl
```

Requires `GUARDIAN_SKEPTIC=gemini`, `GUARDIAN_SKEPTIC_MODEL=gemini-2.5-flash` and the
matching key. 24 skeptic calls per arm, no finder calls. The recording is committed here
because the GitHub artifact it came from expires after seven days.
