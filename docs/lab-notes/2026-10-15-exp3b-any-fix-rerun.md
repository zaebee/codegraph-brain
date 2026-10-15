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

## Result (added after the run; nothing above was changed)

6 sessions, 0 errors, $0.87, cgis at 7a567df, every transcript `apiKeySource: ANTHROPIC_API_KEY`.

| task | exp3 recall (old graph) | exp3b recall (after #638) |
|---|---|---|
| calculate-total-price | 0.83 / 0.83 / 0.83 | 0.83 / 0.83 / 0.83 |
| opensearch-mget | 1.00 / 0.50 / 0.97 | 0.97 / 0.97 / 0.97 |

**Verdict: does not help, as expected.** `calculate-total-price` misses the same FavoritesService
chain in 3/3 runs. The `opensearch-mget` clause is met (no session below 0.90), but with n=3 the
missing 0.50 outlier cannot be attributed to the fix. All three runs miss only
`Worker.reset_showcase_data` (the `cls._run_reset` gap, #637); exp3 run 0 had found it, so the
maximum went down while the median stayed at 0.97.

**What the agent was shown.** In all three `calculate-total-price` sessions, `cgis_analyze_impact`
(text format) ended with this line (verbatim, one line, wrapped here):

```
coverage: 10 of 53 calls unresolved (unresolved_calls_by_name); most often service.get_vehicle (3),
service.list_vehicles (3), service.get_vehicle_detail (2), self.catalog_service.build_cards (1),
service.search_vehicles (1)
```

After #638 the gap is visible: `self.catalog_service.build_cards` is listed. But the line gives a
name, not a place. And **9 of its 10 calls are test code** (`domains/*/tests/test_*.py`), which the
tree above it leaves out ("26 test caller(s) left out"). Neither half of the line applies that screen:
the unresolved count (`unknown_calls_named`) has no test filter, and the total (`walk.calls`) collects
every incoming call before the screen runs, including 27 resolved test calls. The one production call, `self.catalog_service.build_cards` in
`FavoritesService._hydrate` (`favorites/service.py:383`), is exactly the missed chain, and it was
shown fourth of five, among test noise. The JSON format would not have helped: its `top_unresolved`
is also (name, count) pairs.

None of the agents grepped for `catalog_service.build_cards`; their greps searched only
`calculate_total_price|apply_vehicle_prices|_price_group_from_templates`. Two of three opened no
file and saw only grep match lines.

**Next lever (from the criterion): the coverage note, not the resolver.**
(a) Screen both the unresolved count and the total the same way the traversal is screened, with no
test callers. Here the line would have read `1 of 17 calls unresolved … self.catalog_service.build_cards (1)`.
(b) Name each remaining call's caller and file:line; the store already holds `source`, `file_path`
and `line_number`. This is a design diagnosis from three transcripts, not a demonstrated cause.
The test is a probe of the same size after the change.
