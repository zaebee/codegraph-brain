# Agent A/B results

One section per run, newest first. The raw lines are in `results.jsonl`; the
transcripts they point at are git-ignored and stay on the machine that ran them.

## 2026-10-14: `owner-impact-credit-wallet`, after #568 and #550

cgis at fc33b20, Sonnet 5.5, 3 runs per arm, 0 contaminated, about $1.5 in total.
`cgis` was not re-run: it made no cgis call on this task in the run below.

| arm | recall | precision | median $ | median tool calls | cgis calls per run |
|---|---|---|---|---|---|
| control | 1.00 | 1.00 | 0.298 | 7 | — |
| cgis-forced | 1.00 | 1.00 | **0.184** | 5 | 2, 5, 1 |

- `cgis-forced` went from $0.235 to $0.184 median and is now about 38% cheaper
  than control. Control was unchanged ($0.30 to $0.298).
- **#568.** Two of the three sessions opened with
  `app.domains.finance.service.credit_wallet` again, and this time it resolved:
  the first answer was the full chain (6,746 characters), with no not-found
  first. The third tried the bare `credit_wallet`, which is still ambiguous.
- **#550.** That first answer now includes both routes, `get_top_up` and
  `stripe_webhook`. One session finished on a single cgis call. The greps that
  remain are the agent confirming that `FinanceService.top_up` and the worker
  tasks have no further callers, not looking for missing ones.

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
