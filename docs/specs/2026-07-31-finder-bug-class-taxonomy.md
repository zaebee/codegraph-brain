# Finder bug-class taxonomy — gap analysis (#258)

**Status:** research input, **nothing shipped** (2026-07-31)
**Issue:** #258
**Lane:** guardian

## What this is, and what it deliberately is not

#258 asks to broaden the finder's focus areas using
[alibaba/open-code-review](https://github.com/alibaba/open-code-review) (Apache-2.0)
as a source. This document does the mining and the gap analysis. It **does not
change the prompt**, and the reason is in the evidence below: a taxonomy checklist
is not a proven recall lever in this project, and the one adjacent experiment
regressed a class the finder was reliably catching.

What it produces instead is the two things that were missing before that decision
can be made: a grounded class list, and a benchmark fixture that contains a real
instance of the top gap.

## The source

The taxonomy lives in `internal/config/rules/rule_docs/*.md` — per-language rule
documents, not a single ruleset file. Read `python.md` (language-specific) and
`default.md` (language-agnostic axes).

`default.md` gives five axes: **Correctness, Security, Performance,
Maintainability, Test Coverage.**

`python.md` names ten classes:

| # | class | representative rules |
|---|-------|----------------------|
| 1 | Obvious Typos or Spelling Errors | misspellings at declaration sites, in log/exception strings |
| 2 | Dead Code | unreachable branches, unread variables/imports, commented-out blocks |
| 3 | Mutable Default Arguments and Shared State | `def f(x=[])`, class-level mutables, loop-variable closures |
| 4 | Boundary and Edge-Case Handling | empty-collection indexing, off-by-one, `None` reaching non-optional code, **float `==`**, `d[k]` vs `d.get(k)` |
| 5 | Error Handling and Exceptions | bare `except:`, over-broad `except Exception`, silently discarded exceptions, lost traceback, over-wide `try`, `assert` for runtime validation |
| 6 | Identity and Equality Comparisons | `is` against literals, `== True`, `== None` |
| 7 | **Resource Management** | files/sockets/locks/connections without `with`; **"resources acquired in a `try` whose `finally` cleanup is missing or incomplete on the error path"** |
| 8 | Performance | `+=` string building, list membership tests, loop-invariant recomputation, eager f-strings in logging |
| 9 | Concurrency and Async | check-then-act races, blocking calls inside `async def`, **asyncio tasks created and never awaited**, unsynchronised shared state |
| 10 | Security-Sensitive Code | `eval`/`exec`, `subprocess(shell=True)`, `pickle`/`yaml.load` without SafeLoader, string-built SQL, secrets in logs, weak crypto, path traversal |

## A framing incompatibility that must not be copied

`python.md` opens with:

> Favor precision over recall: only raise an issue when you are confident it is a
> real defect, and stay silent when the surrounding context is unclear.

That is the **opposite** of this project's finder, which was deliberately moved to
the max-recall end in #249 (cap removed, confidence gate dropped, "surface every
plausible finding") on the recall-then-filter architecture. Their rules also
repeatedly instruct the model to *confirm with a file read before flagging* —
an agentic capability our finder does not have.

**Borrow the class names. Do not borrow the framing.** Lifting their preamble
would undo #249.

## Gap analysis against the current focus areas

`prompts.py` currently names seven: Logic bugs, Unvalidated external data,
Exact-equality on floats/money, Missing test coverage, Type safety, Library
boundary contracts, Ontology compliance.

Mapped against the source:

| source class | our coverage |
|---|---|
| Boundary and Edge-Case Handling | **covered** — split across "Logic bugs" and "Exact-equality on floats" |
| Test Coverage | **covered** — "Missing test coverage" |
| Security-Sensitive Code | **absent** |
| Concurrency and Async | **absent** |
| Resource Management | **absent** |
| Error Handling and Exceptions | **absent** |
| Mutable Default Arguments | absent |
| Identity and Equality Comparisons | absent |
| Dead Code | absent |
| Performance | absent |
| — | "Type safety" and "Ontology compliance" are ours alone (mypy-strict and the cgis graph contract); no counterpart, and correctly so |

### Four gaps have evidence in this repository, not just in the abstract

- **Resource Management.** PR #278's diff *modified* the
  `client = genai.Client(...)` line in `gemini.py` and left both httpx pools
  unclosed. Guardian reviewed that hunk and returned 10 findings, none about
  resource cleanup. Fixed later by human-directed review as #283. The source's
  wording — cleanup "missing or incomplete on the error path" — even covers the
  follow-on defect found in review of that fix, where a throwing `aclose()`
  skipped `client.close()`.
- **Error Handling and Exceptions.** The recording guard added in #279 was written
  as `except Exception` and was too broad: it swallowed the `ValueError` a
  mistyped path raises. Caught by an independent reviewer, not by the finder.
- **Security-Sensitive Code.** This repo parses YAML (`load_ground_truth`,
  `patterns.yaml`) and shells out to `git` and `gh` throughout `guardian_bench.py`
  and `collector.py`. Two of the source's rules — `yaml.load` without `SafeLoader`,
  `subprocess` with `shell=True` — target exactly those surfaces.
- **Concurrency and Async.** The whole guardian path is async: providers, chunked
  review, `judge_all`'s concurrency. "asyncio tasks created and never awaited" and
  "blocking calls inside `async def`" are live risks here.

The remaining four gaps (mutable defaults, identity comparisons, dead code,
performance) have no observed instance in this codebase. They are candidates, not
findings.

## Why the prompt is not being changed here

Two independent pieces of evidence:

1. **The lever is unproven.** The deep-research synthesis behind #249 ranked levers
   by vote; "refined checklist raised recall 0.66→0.93" was **killed 0-3**, and
   "few-shot beats taxonomy+CoT" was also killed. The defect checklist was
   explicitly demoted to "a light scaffold, NOT the main bet" — the main bets being
   cap removal, the max-recall instruction, and sampling.
2. **The adjacent experiment regressed.** Expanding few-shot with a targeted float
   example did not crack the float class *and* dropped pr-144 from a median 3/5 to
   1/5 — it pulled finder attention away from the yaml class it was catching
   reliably. Reverted on no-evidence-no-ship (#247's over-anchoring lesson, which
   #258 itself cites).

A compact named-class checklist is a weaker intervention than the few-shot that
burned us, so it may well be safe. But "may well be" is not a measurement, and
this system has swung on single-finding deltas before.

## What would make the change decidable

#258 says to validate "once a suitable fixture exists". One now does:
`benchmarks/guardian/pr-278.yaml` carries a real Resource Management defect
(`gemini-client-never-closed`) that the current finder demonstrably missed, on a
diff whose review head is pinned.

Adding the ground truth does not disturb the precision baseline published in #279:
`precision = matched / (matched + noise)` and matched stays 0, so it remains
`0/10 = 0.0`. What it adds is a recall signal — a real **0/1** where an empty
ground truth gave a vacuous 1.0.

The experiment that decides #258:

1. Freeze a finder set on pr-278 with `--record-finder` (no skeptic).
2. Replay with the current prompt: expect recall 0.0 on that entry.
3. Add the compact class checklist and re-run the frozen-set comparison across
   pr-278 **and** the existing entries — the point is not only "does it catch the
   leak" but "does it stop catching what it caught", which is how the float
   experiment failed.
4. Ship only if recall does not regress elsewhere.

Steps 2–4 need a provider key, which is why they are not done here.

## Out of scope

- Any edit to `prompts.py`.
- Adopting the source's precision-over-recall framing, or its file-read-before-flag
  instructions (we have no such capability in the finder).
- Copying rule text verbatim — the source is Apache-2.0 Go/agentic tooling for a
  different stack; #258 asks for the taxonomy only.

---

# Addendum — 2026-08-01: the experiment is now runnable, and a second intervention appeared

Three things changed after this document was written. None of them changes the
conclusion above (**still nothing shipped to `prompts.py`**), but two of them
change what the deciding experiment should measure.

## 1. The top gap now has a fixture

When the table above was written, the Security-Sensitive Code row was **absent**
with no ground truth to test it against; only Resource Management (pr-278) had
one. `benchmarks/guardian/pr-313.yaml` (#315) now carries a real security defect:

    dangling-symlink-write-primitive   (major, category: security)

A `.db` symlink whose target does not exist defeats a path guard —
`is_file()` is False for a dangling link, so the magic-byte check never runs and
SQLite creates the target. Reproduced by execution, not mined from dialogue.

`category: security` was added to `Category` with that fixture. The finder prompt
does **not** offer it — that is this document's subject and still unshipped.
Scoring is unaffected: `_entry_accepts` compares file and line range, never
category.

So the experiment can now measure both evidenced gaps rather than one.

## 2. A second, better-evidenced intervention: exculpating guards

Production runs on 2026-07-31 produced a failure mode this document does not
consider, because it is not a missing class — it is a defect in the classes that
already exist.

On PR #297 the finder returned **12** "Unvalidated dictionary access" findings
against code that validates with `isinstance` one line above the use. On #313 it
returned "floating-point comparison may be flaky" on a line carrying an explicit
`+ 1e-9`. Both trace to focus-area wording that names a *shape* and never names
the guard that makes an instance a non-finding:

> **Unvalidated external data** — … used as a `dict`/… **without first checking
> its type or presence**

The finder matches the shape and does not check whether the guard is present.

This matters for sequencing: **adding classes to a checklist that already
over-fires multiplies noise.** The precision fix should be measured first, or at
least separately — otherwise a recall gain from new classes and a noise change
from the rewrite arrive in the same number and neither can be attributed.

It is also the cheaper experiment. It needs no new fixture: if adding an
exculpating clause to each existing focus area lowers `noise` without lowering
`matched` on the current corpus, that is the whole result.

## 3. Replay already exists

This document says steps 2–4 "need a provider key". True, but worth stating
plainly for whoever picks this up: the *machinery* is not missing.
`uv run --frozen python scripts/guardian_bench.py --replay-finder <recording.json>`
freezes a finder set and judges it, and `load_finder_recording` strips prior
verdicts on read so arms stay comparable.
(#302 was filed claiming this was absent and has been closed as redundant.)

The only blocker is credentials.

## Pre-registered gate

Writing pass/fail criteria before spending money is what made the chunked-review
no-go mechanical rather than motivated (#160). Same discipline here.

**Arm A — exculpating-guard rewrite** (no new classes):
1. mean `noise` per PR drops by ≥ 25% against the frozen baseline
2. `matched` does not drop on any PR
3. pr-141, the noise probe, does not regress

**Arm B — class checklist** (security, concurrency, resource, error-handling;
compact named classes, no per-class few-shot, per #247):
1. pr-278 `gemini-client-never-closed` OR pr-313 `dangling-symlink-write-primitive`
   is matched at least once in three runs — the hypothesis must show where it matters
2. no PR drops more than 0.05 below its baseline recall
3. mean noise does not exceed the Arm A result

Ship each arm only on its own gate. If Arm B passes only with Arm A applied,
say so explicitly rather than reporting the combined delta.

## Still out of scope

Unchanged from above, plus: do not run Arm B before Arm A has a number. The
float few-shot regression happened because two effects were entangled in one
measurement.

---

# Arm A result — 2026-08-01: fails its gate, and shows why

Run with the prod pairing (finder `mistral-medium-latest`, skeptic
`gemini-2.5-flash`), one run per fixture over all eight, baseline and treatment
sweeps back to back.

**Treatment:** each of five focus areas gained one clause naming what makes an
instance *not* a finding — a guard that dominates the use, an existing tolerance
compare, a test that already reaches the path, `Any` as the honest type of
parsed data. No per-class few-shot, per #247.

## The gate says no

| PR | noise | matched |
|---|---|---|
| 122 | 11 → **19** | 4 → 4 |
| 140 | 20 → 13 | 6 → **5** |
| 141 | 7 → **8** | 0 → 0 |
| 142 | 0 → 0 | 0 → 0 |
| 143 | 6 → **14** | 4 → **3** |
| 144 | 6 → 2 | 2 → 2 |
| 278 | 7 → 7 | 1 → 1 |
| **313** | 6 → 4 | **0 → 2** |

```
1. mean noise -25%          FAIL   7.88 -> 8.38  (+6%, wrong direction)
2. matched drops nowhere    FAIL   pr-140, pr-143
3. pr-141 does not regress  FAIL   7 -> 8
```

Three of three. The clauses do not ship.

## The finding inside the failure

**pr-313 went from recall 0.000 to 1.000** — the finder surfaced both ground-truth
entries it had previously missed entirely. Precisely: one is categorised
`security` (`dangling-symlink-write-primitive`) and one `logic`
(`oserror-escapes-the-tool`), so this is one security-class hit plus one logic
hit, not two security hits. That is the fixture added in #315 because the finder
was blind to the class.

And the total is unchanged: **matched 17 → 17**. Two lost on pr-140/143, two
gained on pr-313. Attention moved; precision did not improve.

That is the same mechanism that killed the float few-shot experiment: the model
reallocates focus rather than applying an extra criterion. It is worth stating
plainly because the naive reading of "noise went up, recall moved around" is
"the change did nothing", and that is not what happened — it did something
specific and undesired.

## What this implies for Arm B

It strengthens the case for testing Arm B rather than weakening it. If a clause
that merely *mentions* guards can pull the finder onto a class it was blind to,
an explicit named class is a more direct instrument for the same effect — and
Arm B's gate is written around exactly that (pr-278 or pr-313 matched at least
once in three runs).

It also confirms the sequencing rule already in this document: these two effects
must not be measured together. Had both shipped at once, the pr-313 gain and the
pr-140/143 loss would have arrived as one number.

## Honest limits

- **n=1 per arm.** Finder variance on an unchanged diff has been measured at
  6 → 46 → 36 findings. Criteria 2 and 3 turn on single-finding deltas and are
  fragile at this sample size. Criterion 1 is the sturdier signal: noise moved
  the wrong way, by +6% across eight fixtures.
- Cost: ~1.1M tokens for the two sweeps.

## Why the raw rows are not in results.jsonl

Both arms carry the same `guardian_sha` — the treatment was a working-tree edit,
never committed — so the rows would be indistinguishable once merged into the
corpus. The aggregate above is the record. Anyone repeating this should either
commit the prompt variant behind a flag first, or add an arm label to the
metrics row.

**Methodology note for whoever repeats this:** the first attempt was ruined by
launching the sweep twice against one results file, after an empty (buffered)
log was misread as a dead process. Check for a running bench by process name,
not by looking at its log.

`pgrep -f guardian_bench.py` is **not** enough — measured with the bench fully
stopped it still returns 2, because the shell wrapper running the check carries
the pattern in its own command line. pgrep excludes its own pid, not its
parent's. Match on the process name instead:

```sh
ps -eo comm,cmd | awk '$1 ~ /^python/ && /guardian_bench\.py/' | wc -l
```

Returns 0 when nothing is running. A false positive here is not harmless: it
fires the pre-launch guard and silently skips the sweep.
