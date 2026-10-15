# Exp3b: does the #636 fix change what a forced-cgis agent answers? Pre-registration

Date: 2026-10-15. Committed before any exp3b session. Follows exp3 (#635) and the fix #638 (#636).

## Question

Exp3 traced two forced-cgis misses to confident wrong edges into `typing.Any.*`. #638 turned those
edges into honest unresolved calls (`self.catalog_service.build_cards`, `reservation_client.mget`),
which now appear in `top_unresolved`. The fix does **not** add the missing edges: the receivers are
typed `Any`, so the graph still cannot say where the calls go. The question is whether an agent that
now *sees* the gap follows it.

A graph re-score of the stored exp3 sessions cannot answer this: their answers were given against the
old graph. Fewer wrong edges in the graph is not evidence that answers improved.

## Design

- Arm `cgis-forced`, identical to exp3. cgis at this commit (main 4cb43b6 plus this note), owner-api
  8de22452, `claude-sonnet-5-5`, 3 runs each, organisation API key (every transcript must show
  `apiKeySource: ANTHROPIC_API_KEY`).
- Tasks: `owner-exp1-big-ind-calculate-total-price` and `owner-exp1-big-ind-opensearch-mget`, with
  gold unchanged from exp3.
- Comparison: the exp3 forced sessions for the same tasks (`results-exp3-forced.jsonl`). The only
  difference is the cgis version. n=3 per task, so this is a probe, not a gate on the positioning.

## Criterion

- **The fix helps the agent** if median recall on `calculate-total-price` rises from 0.83 to ≥ 0.95
  **and** no `opensearch-mget` session falls below 0.90 (exp3 had one at 0.50).
- **It does not** if `calculate-total-price` median recall stays below 0.95. Then the agent sees the
  unresolved call and does not follow it, as in exp3. The next lever is the coverage note (name the
  gap and say how to close it), not the resolver.
- Expected outcome, stated before the run: **does not help**. In exp3, agents named unresolved gaps
  but did not follow them.
- Also recorded, descriptively: whether each answer mentions `build_cards` / `mget` as unresolved, and
  files read after the first cgis call.
