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
