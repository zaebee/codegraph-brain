# Exp3c: does naming where a gap is make the agent follow it? Two arms. Pre-registration

Date: 2026-10-15. Committed before any exp3c session. Follows exp3b (#640) and #641 (PR #644).

## Question

In exp3b the unresolved call `self.catalog_service.build_cards` was in the impact answer's coverage
line, but only as a name: fourth of five, among test-code noise. No agent followed it, and
`calculate-total-price` recall stayed at 0.83 in 3/3 runs. #644 makes two changes:

1. **Coverage line.** Test callers are screened out of both halves of the count, and each unresolved
   name carries its caller and file:line.
2. **Server instructions.** One added sentence: when such a site could reach the asked symbol, read
   that line and follow it; naming the gap is not enough.

Measured together, a rise could not be attributed to either change, so there are two arms.

## Design

- **Arm A (`sites-only`):** #644's code, with the server instructions as on main. One local commit
  reverts `src/cgis/api/server_instructions.py` to main; its sha is recorded in every result row.
- **Arm B (`sites+directive`):** #644 as reviewed: the branch head that carries this note. Its sha is
  recorded in every result row. Arm A is rebuilt on that same head, so the two differ in one file.
- Both arms:
  - harness arm `cgis-forced`, as in exp3 and exp3b;
  - task `owner-exp1-big-ind-calculate-total-price` against owner-api 8de22452, gold unchanged;
  - model `claude-sonnet-5-5`, 3 runs per arm, on the organisation key (every transcript must show
    `apiKeySource: ANTHROPIC_API_KEY`).
- **Baseline:** exp3b (`results-exp3b-forced.jsonl`), with recall 0.83 / 0.83 / 0.83. Arm A differs
  from exp3b only in the coverage output: the text line, and in json the new `sites` and the
  screened counts. Arm B also changes the server instructions that exp3 and
  exp3b used. That is a stated limitation of comparing B with them.

## Criterion (per arm)

- **Helps:** median recall ≥ 0.95.
- **Does not help:** median recall < 0.95.
- **Reading:**
  - A helps → the place is enough.
  - Only B helps → the directive is what moves the agent.
  - Only A helps → the directive hurts. That is unexpected; read the B transcripts before concluding.
  - Neither helps → the next lever is outside the coverage line.

  With n=3 per arm this is a probe, not a gate.
- **Also recorded, descriptively, per session:**
  - whether the agent read `favorites/service.py`, or grepped `catalog_service.build_cards`, after its
    first cgis call;
  - cost and turns.

## Result (added after the run; nothing above was changed)

6 sessions, 0 errors, $0.67 ($0.33 arm A, $0.34 arm B). Every transcript shows
`apiKeySource: ANTHROPIC_API_KEY`. Arm A ran at 0edeeb1, arm B at 8a09ee7. A first launch of
arm A failed before any session started: `/tmp` was full and `cgis ingest` could not write the
graph. Its three rows had no cost and no transcript, and were removed before the relaunch.

| | recall | cost | turns |
|---|---|---|---|
| exp3b (baseline) | 0.83 / 0.83 / 0.83 | 0.099 median | — |
| arm A, sites only | **1.00 / 1.00 / 1.00** | 0.126 / 0.103 / 0.098 | 6 / 6 / 6 |
| arm B, sites + directive | **1.00 / 1.00 / 1.00** | 0.130 / 0.115 / 0.097 | 10 / 7 / 5 |

**Verdict: both arms help (median recall 1.00 ≥ 0.95). By the pre-registered reading, the place is
enough.** The directive is not needed for this task.

**How the agent closed the gap: through the graph, not the file.** All three arm A sessions took
the same four steps:

1. `Grep calculate_total_price`.
2. `cgis_analyze_impact(PricingService.calculate_total_price)`. Its coverage line now names
   `self.catalog_service.build_cards (1) at FavoritesService._hydrate …/favorites/service.py:383`.
3. `cgis_analyze_impact(FavoritesService._hydrate)`.
4. `cgis_analyze_impact(CatalogService.build_cards)`.

None opened `favorites/service.py`. The caller named in the site was enough to ask the graph again.
Arm B took the same step in all three sessions. Two of them also grepped or read the favorites
source, which is the directive at work, and spent more turns (10 / 7 / 5 against 6 / 6 / 6) at
about the same cost.

**Limits.**
- n=3 per arm, on one task. This is the task where exp3 and exp3b failed, and the one #641 was
  built from, so the result shows the mechanism works on its motivating case. It does not show
  that it generalises.
- The other exp3 misses come from different gaps, which this change only makes visible: the
  decorator wrapper, `cls.` calls and polymorphic dispatch (#637).
- Whether arm A's behaviour holds unforced, and across the exp3 primary set, needs the adoption
  experiment.
- Arm B's directive is not shown to be useless. This task did not need it.
