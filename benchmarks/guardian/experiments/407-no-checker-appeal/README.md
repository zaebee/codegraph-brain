# Telling the skeptic that no checker ran (#407)

Measuring #401 needed a control arm: the same 24 findings judged with **no checker output at
all**. In it, 6 of 24 rationales cited `mypy` or `ruff` anyway — every one to *confirm* a
false claim, on a conditional the model could not check:

> The use of `Any` does indeed bypass strict type checking in mypy … and **if** `mypy
> --strict` is configured to disallow `Any` explicitly, it would be flagged.

`_evidence_section(None)` returned the empty string, so the skeptic was told nothing about
checkers and supplied its own. Two prompts were tried against that.

## Method

The replay control arm is exactly this configuration, so each attempt costs 24 skeptic calls
and no finder call. Same recording (`../401-evidence/pr-399-recording.json`), same absence of
evidence, one variable: the prompt.

Registered before running: **primary** — how many of the six checker-appealing findings stop
being confirmed on that basis; **recall guard** — finding 4, the one substantive finding of
the 24, must stay confirmed; **noise floor** — two draws of the original prompt moved 4 and 1
verdicts out of 24, so anything smaller than that is not a result.

## Attempt 1 — a prohibition. Failed.

> Do not rest a verdict on what a checker would report — not to confirm a finding … and not
> to refute one …

| | original | prohibition |
|---|---:|---:|
| rationales citing a checker | 6/24 | 5/24 |
| the six, still confirmed | 6 | 5 |
| finding 4 | confirmed | confirmed |

Inside the noise floor, and the text got **worse**. Finding 0's conditional became a flat
assertion:

> This is an issue that would be caught by the project's **mandatory `mypy --strict` gate**.

There is no such gate over that file: `make type-check` runs `mypy src`, and `pyproject.toml`
excludes `scripts`. Told what not to do, the model complied in form and confirmed anyway —
with more confidence than before.

## Attempt 2 — a directive. Worked.

> If a finding rests entirely on what a checker reports … then it cannot be settled here.
> Mark it 'uncertain'. Not 'confirmed': you have not seen a checker agree. Not 'refuted': you
> have not seen one disagree either.

| finding | original control | directive |
|---|---|---|
| 0, 2, 3, 5, 15 — claim a mypy rule | confirmed | **uncertain** |
| 12 — `sys.path` "violates type safety" | confirmed | confirmed |
| **4 — the one true finding** | confirmed | **confirmed** ✅ |
| 21, 22 — propose relaxing a ratchet | confirmed | **refuted** |

Five of six moved, all in the same direction, all on cited-checker rows: outside the 1–4
noise floor and mechanistically legible. Finding 0 now states its position instead of
inventing a gate:

> Without access to the project's `mypy` configuration or output, it is not possible to
> confirm if this specific usage would be flagged.

The recall guard held, with the correct reasoning — `CalledProcessError` is raised and not
caught, which is the defect that was really fixed in #399.

### An unregistered gain

Findings 21 and 22 were refuted. Those are the two the RFC singled out as **beyond any
tool**: they proposed relaxing a `>= 72` ratchet floor and an `ambiguous_hits` invariant, and
were wrong about *intent* rather than about behaviour. The model got there on its own:

> The assertion is a deliberate guardrail … its failure due to a diminished corpus is
> **intended behavior, not a defect**.

Not predicted, not registered, one draw — recorded as an observation, not a claim.

## What this does not buy

**`uncertain` is kept.** `apply_judgements` multiplies confidence by 0.9 as a ranking signal
and nothing more, and `render_review_body` takes `threshold: int = 0`. So all five findings
are still posted to the pull request; only their ordering changes. The verdict is now
truthful — the reader's experience is very nearly the same.

That makes this an epistemic fix, not a noise fix. Suppressing unadjudicable findings is a
separate knob (the `impact_threshold` that #246 §3.5 records as shipping inert), and it
should be decided on its own evidence rather than folded in here.

**One draw per arm, and a recall denominator of one.** The primary effect is well outside the
noise floor; nothing else here is.

**`skeptic.py` is inside the review-fingerprint closure** (43 modules), so this re-mints every
reviewer identity — by design (#375).

## Reproducing

From the repository root — the paths are root-relative, because the command above them is:

```bash
scripts/guardian_replay_skeptic.py \
    --recording benchmarks/guardian/experiments/401-evidence/pr-399-recording.json \
    --control --out rows.jsonl
```

With `GUARDIAN_SKEPTIC=gemini`, `GUARDIAN_SKEPTIC_MODEL=gemini-2.5-flash`. 24 skeptic calls,
no finder calls.
