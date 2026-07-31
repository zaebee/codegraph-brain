# Lab note: the chunked-review experiment — a negative result worth keeping

**Date:** 2026-06-11
**Experiment:** #154 slices 1–2 · **Code:** PR #157 (`6178bae`), PR #159 (`2030eb8`)
**Verdict:** gate FAILED 3/4 — feature stays behind a flag, off in production
**Specs:** [`2026-06-11-guardian-chunker-design.md`](../specs/2026-06-11-guardian-chunker-design.md) ·
[`2026-06-11-guardian-chunked-review-design.md`](../specs/2026-06-11-guardian-chunked-review-design.md)

The headline: the hypothesis survived contact with reality only partially, and the
honest negative result is the most valuable artifact.

## The problem

Guardian (our in-repo LLM reviewer, replacing gemini-code-assist before its
2026-07-17 sunset) reliably LGTMs large PRs. Baseline bench (6 replayed PRs with
hand-curated ground truth, 3 runs each): mean recall 0.22, but **recall = 0 on all
three largest PRs** (36–62K prompt tokens). Plan-2 ablations proved more context
doesn't help: feeding 94K tokens of full files still produced LGTMs.

Diagnosis: **attention dilution** — the finder skims, it doesn't read.

## The hypothesis (#154)

If the finder can't read a big diff, give it small ones: split changed files into
connected components over the code graph (IMPORTS/CALLS from `cgis ingest`), run a
finder pass per chunk with a per-chunk context budget, merge, dedup, run ONE
skeptic pass, post one report. Each chunk = a small, complete world.

## What we built

- **Slice 1 (PR #157, `6178bae`)** — `chunker.py`, pure logic. Per-file diff
  splitting (rename/deletion/binary/git-quoted-path handling), union-find over
  graph edges, test-pairing heuristic, deterministic ordering, honest degradation
  to isolated chunks when the graph DB is missing.
- **Slice 2 (PR #159, `2030eb8`)** — wiring behind `GUARDIAN_FEATURES=chunked`.
  `run_review_routed` entry point, per-chunk finder passes with per-chunk
  `full_files` (120K char budget) + impact graph, out-of-chunk hallucination
  filter with LLM-path-artifact normalization (`a/x.py` → `x.py`),
  `(file,line,category)` dedup, single skeptic over finding-bearing chunks only,
  `MAX_CHUNKS=8` cap with overflow merge, `cumulative_usage` token accounting
  (which incidentally fixed a latent retry-loses-tokens bug).

Both slices SDD-executed (subagent implementer + spec reviewer + quality
reviewer), bench-gated by spec §7 **before** enabling anything in prod.

## The gate (pre-registered, spec §7)

1. mean recall (6 PRs) ≥ 0.27 — baseline + 0.05
2. mean recall over large PRs {140, 143, 144} > 0 — the hypothesis must show where it matters
3. mean noise ≤ 1.5/PR
4. no PR drops > 0.05 below its baseline

## The result: FAIL 3/4

18 runs (6 PRs × 3, finder `gemini-2.5-flash`, skeptic `gemini-3.5-flash`), data in
`benchmarks/guardian/results.jsonl`:

| PR | chunks | recall base → chunked | noise base → chunked |
|---|---|---|---|
| 122 | 8 (cap) | 0.152 → 0.152 | 1.0 → **6.0** |
| 140 | 5 | 0 → **0.067** ✨ | 3.3 → 3.3 |
| 141 (noise probe) | 5 | 1.0 → 1.0 | 0.7 → 2.7 |
| 142 | 1 (degenerate) | 0.167 → 0 | 0 → 0 |
| 143 | 7 | 0 → 0 | 0 → 2.7 |
| 144 | 5 | 0 → 0 | 0.7 → 1.3 |

Mean recall 0.203 (❌ vs 0.27), large-PR recall 0.022 (✅ — but only PR 140
contributed), noise 2.67/PR (❌ vs 1.5), PR 142 regression (❌, with a variance
caveat: its baseline rested on one lucky 0.5 run out of three).

## What we learned

1. **Attention dilution is real but not the whole story.** Chunking produced the
   first-ever nonzero recall on a large PR (140). But 143/144 stayed at zero across
   21 small, focused finder calls. Those ground-truth bugs are invisible to this
   finder *regardless of context size* — a model/prompt capability ceiling, not a
   context problem.
2. **Chunking multiplies noise superlinearly.** Every chunk invents its own false
   positives (122: 8 chunks → noise 6.0/run). A single skeptic pass over
   concatenated chunk contexts can't compensate.
3. **Pre-registered gates work.** Writing pass/fail criteria into the spec before
   spending money made the no-go decision mechanical instead of motivated. The flag
   stays OFF in prod; the code sleeps behind it, fully tested, waiting for a better
   noise story.
4. **Honest degradation pays off.** Every failure mode (no graph DB, flaky chunk,
   unparsable finder output, dead skeptic) degrades to something useful instead of
   crashing — which also made the bench itself survivable.

## Where this goes next (slice 3 candidates, not committed)

- Noise mitigation: stricter per-chunk finder prompt, per-chunk findings cap,
  skeptic hardening for chunked volume.
- Ensemble finder (#155): union of N runs matched *different* GT findings
  (122: 0.09/0.09/0.18 → union ~0.27) — possibly a cheaper recall lever than chunking.
- Eyeball the 143/144 ground truths: if they need cross-file reasoning the finder
  can't do, no amount of context plumbing will fix it.
- Quotient-level "architectural chunk" (domain-graph drift as its own review unit).

Total experiment cost: ~5 PLN of gemini billing for the slice-2 bench (1.03M prompt
tokens), on top of Plan-2's bench budget. Money well burned.

## Reproducing these numbers

`benchmarks/guardian/results.jsonl` is committed, so the chunked side of this
experiment is re-derivable. Group rows by `pr`, take the 3 rows whose `features`
field contains `chunked` (all share `guardian_sha` `a866a2ac`), and average per PR:

| Figure | Note | Recomputed |
|---|---|---|
| mean recall (6 PRs) | 0.203 | 0.203 |
| mean noise / PR | 2.67 | 2.667 |
| large-PR recall {140,143,144} | 0.022 | 0.022 |

Every cell of the table's **chunked** column reproduces exactly.

**The `base` column does not.** No single `guardian_sha` slice of the
`features == ""` rows reproduces it, and the baseline headline of 0.22 mean recall
does not fall out of any of them either — the two candidate slices with exactly 3
runs per PR give 0.191 and 0.167, and the earliest slice gives 0.271 on uneven run
counts. The file has also been appended to since (there are July 2026 rows from
later work), so the working set used to compute the baseline in June is no longer
recoverable from it.

This is recorded rather than corrected: a lab journal is a historical record, and
silently restating the baseline would be worse than flagging it. The gate
conclusions do not rest on it — gates 1 and 3 failed on absolute values (0.203 <
0.27, 2.67 > 1.5), and both reproduce. Gate 4 (per-PR regression vs baseline) is
the one that cannot be independently re-checked today.

## Refs

#154, #155, #160 · PR #157, PR #159
