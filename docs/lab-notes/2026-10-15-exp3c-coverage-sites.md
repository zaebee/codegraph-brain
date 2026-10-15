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
- **Arm B (`sites+directive`):** #644 as reviewed.
- Both arms:
  - harness arm `cgis-forced`, as in exp3 and exp3b;
  - task `owner-exp1-big-ind-calculate-total-price` against owner-api 8de22452, gold unchanged;
  - model `claude-sonnet-5-5`, 3 runs per arm, on the organisation key (every transcript must show
    `apiKeySource: ANTHROPIC_API_KEY`).
- **Baseline:** exp3b (`results-exp3b-forced.jsonl`), with recall 0.83 / 0.83 / 0.83. Arm A differs
  from exp3b only in the coverage line. Arm B also changes the server instructions that exp3 and
  exp3b used. That is a stated limitation of comparing B with them.

## Criterion (per arm)

- **Helps:** median recall ≥ 0.95.
- **Does not help:** median recall < 0.95.
- **Reading:**
  - A helps → the place is enough.
  - Only B helps → the directive is what moves the agent.
  - Neither helps → the next lever is outside the coverage line.

  With n=3 per arm this is a probe, not a gate.
- **Also recorded, descriptively, per session:**
  - whether the agent read `favorites/service.py`, or grepped `catalog_service.build_cards`, after its
    first cgis call;
  - cost and turns.
