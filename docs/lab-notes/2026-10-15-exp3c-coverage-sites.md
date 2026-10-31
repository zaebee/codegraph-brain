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
| exp3b (baseline) | 0.83 / 0.83 / 0.83 | 0.209 / 0.098 / 0.099 | 7 / 5 / 5 |
| arm A, coverage change only | **1.00 / 1.00 / 1.00** | 0.126 / 0.103 / 0.098 | 6 / 6 / 6 |
| arm B, coverage change + directive | **1.00 / 1.00 / 1.00** | 0.130 / 0.115 / 0.097 | 10 / 7 / 5 |

**Verdict: both arms help (median recall 1.00 ≥ 0.95).** By the pre-registered reading, the
coverage-line change is enough on this task and the directive was not needed. The two halves of
that change are **not separated**. Arm A differs from exp3b in two ways. Test callers are screened
out, so the line went from `10 of 53 …` (build_cards fourth of five, among test noise) to
`1 of 17 …`, with build_cards the only name. And the caller is named. Which of the two did it
cannot be told: the pre-registration bundled both into arm A. No agent went to the cited line (383);
the caller name was what they followed. With 3/3 against 3/3 at identical recall, this says nothing
about the size of the effect.

**What the agents saw and did.** Every session's impact answer on `calculate_total_price` ended
with (verbatim, one line, wrapped here):

```
coverage: 1 of 17 calls unresolved (unresolved_calls_by_name); most often
self.catalog_service.build_cards (1) at FavoritesService._hydrate domains/favorites/service.py:383
```

All three arm A sessions took the same four steps:

1. `Grep calculate_total_price`.
2. `cgis_analyze_impact(PricingService.calculate_total_price)`.
3. `cgis_analyze_impact(FavoritesService._hydrate)`.
4. `cgis_analyze_impact(CatalogService.build_cards)`.

None opened a file: no Read, no Bash, one grep. They closed the gap by asking the graph again about
the named caller.

In arm B, two sessions asked the graph about `_hydrate`. B-2 reached recall 1.00 with two cgis
calls, so arm A's step 4 was not needed. The third session, B-0, asked about `build_cards` first,
then found `_hydrate` with grep and `sed` on `favorites/service.py` and `routes.py`. Only B-0 read
the favorites source. B-1 grepped the chain across the repo, `build_cards` included, and listed
function definitions in `catalog/service.py` and `vehicle/service.py` with awk. Both B-0 and B-1
grepped `build_cards`, the pre-registered descriptive signal; no arm A session did. Whether either is the
directive at work cannot be told at n=3. B-0 and B-1 spent more turns (10 and 7, against 6) and
more money (0.130 and 0.115, against an arm A median of 0.103). B-2 spent less: 5 turns, 0.097.

B-0's last two impact answers said "⚠ Graph is stale: 1 changed, 0 missing since ingest". Its
shell calls look read-only, so the cause is unknown. Recall was unaffected.

**Limits.**
- The MCP server instructions are not recorded in the transcripts: the init event has no
  instructions field. The arm difference is inferred from each row's `cgis_sha` (0edeeb1 vs
  8a09ee7), from the harness starting `cgis-mcp` beside the running interpreter, and from arm A's
  editable install pointing at its own worktree. It was not observed.
- The three removed failed rows were not kept, so that claim cannot be audited. They were written
  by `failure_row` (`is_error` and `failed`, no cost, no transcript), and their tail was the ingest
  log ending at "Resolution complete". Keeping them would have cost nothing; next time they stay.
- n=3 per arm, on one task. This is the task where exp3 and exp3b failed, and the one #641 was built
  from. The result shows the change works on its motivating case. It does not show that it
  generalises.
- The other exp3 misses come from gaps this change only makes visible: the decorator wrapper,
  `cls.` calls and polymorphic dispatch (#637).
- Whether this holds unforced, and across the exp3 primary set, needs the adoption experiment.
- The directive is not shown to be useless. This task did not need it.

<!-- START_CGIS_RELATED -->
**Related:** [exp3b-any-fix-rerun](2026-10-15-exp3b-any-fix-rerun.md)

**Code:** [`src/cgis/api/`](../../src/cgis/api/)

<!-- END_CGIS_RELATED -->
