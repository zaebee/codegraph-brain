# Agent A/B results

One section per run, newest first. The raw lines are in `results.jsonl`; the
transcripts they point at are git-ignored and stay on the machine that ran them.

## 2026-10-13: `owner-impact-credit-wallet`, after #563 and #565

cgis at 3c37874, Sonnet 5.5, 3 runs per arm, 0 contaminated, about $2.5 in total.

| arm | recall | precision | median $ | median tool calls | cgis calls per run |
|---|---|---|---|---|---|
| control | 1.00 | 1.00 | 0.30 | 7 | — |
| cgis | 1.00 | 1.00 | 0.30 | 6 | 0, 0, 0 |
| cgis-forced | 1.00 | 1.00 | 0.235 | 8 | 3, 11, 3 |

- **#551.** The call that returned 51,624 characters in the first pilot,
  `cgis_analyze_impact(domains.finance.service.credit_wallet, depth=8, json)`,
  returned 6,136 here. `cgis-forced` went from more expensive than control
  ($0.207 vs $0.195) to cheaper. Control ranged from $0.198 to $0.327 across
  three runs, so this shows the direction, not the size.
- **#542.** The `cgis` arm now gets server instructions in `initialize`, and its
  server reported `connected` every time. It still made no cgis call: the task
  is solvable by grep in about seven calls, and the agent took that route.
- **#568.** Every forced session's first call was
  `app.domains.finance.service.credit_wallet`, built from the file path, which
  is not found because the graph is ingested from `ownima-backend/app`. That
  cost one or two calls per session before the agent found the real FQN.
- Every arm recovered the two routes the graph misses at this sha
  (`get_top_up`, `stripe_webhook`) by grepping for them.
