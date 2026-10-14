# Agent A/B results

One section per run, newest first. The raw lines are in `results.jsonl`; the
transcripts they point at are git-ignored and stay on the machine that ran them.

## 2026-10-14: does one instruction line get the agent to call cgis?

cgis at 5a5c836, `cgis-instructed` — the `cgis` arm plus one appended system-prompt
line, "query cgis first, then read source only to confirm", the nearest stand-in for
a line in a project's CLAUDE.md. Same two impact tasks, 3 runs each, about $0.8.

| task | cgis calls per run | cost per run |
|---|---|---|
| `cgis-impact-drift-same-name` | 0, 0, **1** | 0.184, 0.148, **0.075** |
| `cgis-impact-transitive-tsconfig` | **1**, 0, 0 | **0.099**, 0.154, 0.153 |

- **The line moves the agent a little: 2 of 6 sessions called the graph**, against 0
  of 18 without it. Both made one call, and both were the cheapest runs of the
  six, at the level of `cgis-forced` (0.085 / 0.091). The other four ignored the
  line and cost more than plain `cgis` (median 0.150 against 0.126–0.138).
- So an instruction is a coin toss weighted against the graph; the hook that refuses
  Read/Grep/Bash until one cgis call (`cgis-forced`) is what delivers the saving
  reliably. A project wanting the gain needs that hook, not a sentence.
- One run is marked `contaminated` with no cgis CLI call in it: on a task in this
  repository every path contains `cgis/`, and the guard's predicate took the agent's
  own `grep`/`sed` over `src/cgis/...` for one. A harness false positive, kept out of
  the medians by the report as usual.

## 2026-10-14: does a short tool menu get an unprompted agent to call cgis? (#542 item 3)

cgis at dbf3de9 (the `cgis-narrow` arm of #586), Sonnet 5.5, 3 runs per arm, 0 failed,
0 contaminated, about $1.6. One probe session confirmed the narrowing reached the
client: the `init` event listed exactly `mcp__cgis__cgis_analyze_impact`,
`mcp__cgis__cgis_find_symbol`, `mcp__cgis__cgis_trace_flow`.

| task | arm | cgis calls | recall | precision | median $ |
|---|---|---|---|---|---|
| `cgis-impact-drift-same-name` | cgis (15 tools) | 0, 0, 0 | 1.00 | 1.00 | 0.126 |
| | cgis-narrow (3 tools) | 0, 0, 0 | 1.00 | 1.00 | 0.125 |
| `cgis-impact-transitive-tsconfig` | cgis | 0, 0, 0 | 1.00 | 1.00 | 0.138 |
| | cgis-narrow | 0, 0, 0 | 1.00 | 1.00 | 0.154 |

- **Refuted.** Three tools instead of fifteen changed nothing: no unprompted
  session called the graph, here or in any of the six `cgis` sessions before it —
  0 of 18 today, across four tasks. The server instructions (#542 item 1) did not
  move it either. Item 3's smaller default set has no evidence behind it.
- These are the two tasks where `cgis-forced` was about 30% cheaper than control
  (#579), so the graph would have paid; the agent never looked.
- A likely reason sits in the client: Claude Code defers MCP tools, listing names
  only, so a first call needs a ToolSearch to load the schema — every forced
  session made one. An agent holding Grep and Bash, with a task grep finishes in
  7–13 calls, does not take that step unasked.
- Confound, as the README notes: the instructions and the plugin skill still name
  all fifteen tools beside the three offered.

## 2026-10-14: `cgis-flow-ingest-to-sqlite` after #581 (text format, trace budget)

cgis at a1dd2b8, Sonnet 5.5, 3 runs per arm, 0 failed, 0 contaminated, about $0.6.

| arm | recall | precision | median $ | trace answer |
|---|---|---|---|---|
| control | 1.00 | 0.94 | **0.101** | — |
| cgis-forced, before (#579) | 1.00 | 0.52 | 0.167 | 32,637 chars of json |
| cgis-forced, now | 1.00 | 0.58 | 0.112 | 6.3–8.9k chars of text |

- **Size: fixed.** All three sessions asked for `output_format="text"` on their
  own (the server instructions now say to), and the answer shrank about 4×.
  Cost fell by a third, to 11% above control.
- **Precision: not fixed.** Given every node the root reaches, the agent still
  lists most of them (17–21 outside the key: `_compute_hash`,
  `_is_noop_incremental`, `ResolverEngine` helpers). Control reads the code and
  names the spine. The format no longer costs the agent; deciding which
  branches are the main path is the part the graph does not do.
- Two sessions first called `cgis_find_symbol(query="ingest", fqn_prefix="src.cgis")`
  and got nothing: #568 taught `resolve_fqn` to drop a directory above the
  graph, but `find_symbol`'s `fqn_prefix` filter was not given the same rule.

## 2026-10-14: three cgis tasks with a deep chain

cgis at bc303eb, Sonnet 5.5, 3 runs per arm, 0 failed, 0 contaminated, about $2.3.
None of these tasks was used to find the recent fixes.

| task | arm | recall | precision | median $ | median tool calls | cgis calls |
|---|---|---|---|---|---|---|
| `cgis-impact-drift-same-name` | control | 1.00 | 1.00 | 0.124 | 9 | — |
| | cgis-forced | 1.00 | 1.00 | **0.085** | 3 | 1, 1, 1 |
| `cgis-impact-transitive-tsconfig` | control | 1.00 | 1.00 | 0.126 | 7 | — |
| | cgis-forced | 1.00 | 1.00 | **0.091** | 4 | 1, 2, 1 |
| `cgis-flow-ingest-to-sqlite` | control | 1.00 | 0.86 | **0.112** | 5 | — |
| | cgis-forced | 1.00 | 0.52 | 0.167 | 4 | 2, 2, 2 |

- **Impact across several hops pays:** about 30% cheaper on both impact tasks,
  with one cgis call doing what took control 7–9 greps and reads. Together with
  `credit_wallet`, that makes three impact tasks out of three where the graph
  wins once the question needs more than one hop.
- **Flow does not, and the cause is the answer's size.** Every forced session
  called `cgis_trace_flow(cgis.cli.ingest, depth=3, json)` and got **32,637
  characters**, the bloat #551 fixed for impact only: trace has no node budget
  and no test filter. Given everything downstream, the agent cannot tell the
  main path from side branches and names them all (precision 0.52, 20 extra
  symbols such as every `ResolverEngine._resolved_*_edge`).
- `occupied_window` (above) and these together say where cgis earns its place:
  multi-hop impact. A one-hop caller list and a "main path" flow question are
  not where it helps today.

## 2026-10-14: `owner-impact-occupied-window-same-name`, a task no fix was tuned on

cgis at 827143e (after #551, #568, #550, #545), Sonnet 5.5, 0 contaminated. The
three fixes before this were all found in `credit_wallet` transcripts and measured
there; this task checks whether the gain carries to a question they never saw.

| arm | runs | recall | precision | median $ | median tool calls | cgis calls per run |
|---|---|---|---|---|---|---|
| control | 3 | 1.00 | 1.00 | **0.130** | 2 | — |
| cgis | 3 | 1.00 | 1.00 | 0.145 | 4 | 0, 0, 0 |
| cgis-forced | 2 | 1.00 | 1.00 | 0.135 | 3.5 | 2, 1 |

- **It does not carry.** On "list the direct callers", with a same-named
  function in another module as the trap, control answered with one grep and
  one look at the imports, and was the cheapest arm. The graph names the four
  callers exactly (`impact depth=1`, 2.5k characters), but there is nothing
  for it to save on a one-hop question that grep plus the imports already
  answer.
- The `credit_wallet` gain came from a deep transitive chain through DI. That
  is where the graph pays, so the question for #542's default tool set is
  which kinds of question agents ask, not whether cgis is cheaper in general.
- `cgis` made no graph call again: 0 in 6 sessions across the two tasks.
- `cgis-forced` has 2 runs, not 3. The host ran out of memory, and earlyoom
  sent SIGTERM to the third session's `cgis ingest`, which aborted the runner
  twice. Not a cgis failure. The runner stops at the first failed ingest
  instead of recording it and going on.

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
