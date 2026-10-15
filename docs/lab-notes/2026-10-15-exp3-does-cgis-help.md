# Exp3: does cgis help an agent answer impact questions? Pre-registration, with a kill criterion

Date: 2026-10-15. Committed before any exp3 session. Follows exp1 (#633) and exp2 (#634), which
found a control agent with no cgis at the recall ceiling on read-only "who can end up calling X".

## Question and decision

Does giving the agent the cgis graph lower the cost of an impact question at equal recall, on the tasks
where a graph should help most (big radius, indirect or dynamic chains)?

**The decision is fixed now.** If the primary gate fails, cgis's positioning as "context for coding
agents" is removed from README and the MCP server instructions, and cgis is positioned as an
architecture gate and metrics tool (drift ratchet, forbidden edges, whole-graph metrics). If the gate
passes, the positioning stays and the next question is adoption: would an agent use cgis unforced.

## Design

- **Arm:** `cgis-forced`. The guard refuses Read, Grep, Glob and Bash until the session has made one
  cgis call. Its system prompt also tells the agent to try cgis first. The graph is built by this
  checkout's `cgis ingest` before the clock starts; ingest time is recorded but is not billed in
  `cost_usd`. This arm is an **upper bound** on the graph's usefulness: if even forced use does not
  pay, unforced use will not.
- **cgis version:** this checkout (main at c74c90d), the same for every task.
- **Tasks:** all 37 tasks with validated gold. That is the 20 `owner-exp1-*` tasks (owner-api
  8de22452) and the 17 `cgis-exp2-*` tasks (cgis a5e175e), with their gold as of main at c74c90d,
  including both post-review corrections.
- **Runs:** 3 per task, `claude-sonnet-5-5`, `--max-budget-usd 2.0`, on the organisation API key.
  Every transcript must show `apiKeySource: ANTHROPIC_API_KEY`.
- **Control:** the stored control sessions from `results-exp1-full.jsonl` and `results-exp2-full.jsonl`.
  They used the same model, the same harness and allowed tools, and ran the same day. Control cost is
  taken as stored. Control recall is taken as stored, except for the two tasks whose gold was corrected
  after their runs. For `owner-exp1-big-ind-thread-for` the declared rescoring is 1.00 / 1.00 / 0.949.
  For `cgis-exp2-dir-extractors`, recall is unchanged at 1.00 and only precision changed.
  **Limitation:** the two arms are not concurrent. Same-day drift in model serving is assumed
  negligible. The control transcripts were not kept.
- **Primary set (14 tasks):** the 11 `owner-exp1-big-ind-*` tasks and the 3 `cgis-exp2-desc-*` tasks,
  i.e. big radius with indirect or dynamic chains.

## Gates

Per task: cost ratio = median cost (forced) / median cost (control); recall difference = median recall
(forced) − median recall (control).

1. **Primary, the kill criterion.** Over the 14 primary tasks, cgis **helps** if the median cost ratio
   is ≤ 0.80 **and** the median recall difference is ≥ −0.02. Otherwise cgis **does not help**, and the
   decision above applies. A bootstrap 95% CI over tasks is reported. If the CI straddles 0.80, the
   verdict still follows the point estimate, as pre-registered, and the CI is stated beside it.
2. **Secondary, descriptive:** the same ratios over all 37 tasks, by cell (exp1 four cells, exp2
   direct / indirect / descriptive). Also: cgis calls per session, files read, turns, wall time, ingest
   time, and whether the agent's answer relied on cgis output (`allocation`).
3. **Precision**, so that forced use is not paid for in wrong extra names. Report median precision
   per arm, primary set.

## Analysis

A script committed verbatim under `benchmarks/agent_ab/analysis/exp3_gates.py` before the run.

## Result (added after the run; nothing above was changed)

111 forced sessions, 0 errors, 0 contaminated, every session made at least one cgis call, $10.71.
All 111 transcripts show `apiKeySource: ANTHROPIC_API_KEY`; served model `claude-sonnet-5-5`.
Output of `exp3_gates.py`, unedited where quoted:

```
PRIMARY (n=14): median cost ratio 0.631 (95% CI 0.50-0.76), median Δrecall +0.000 -> HELPS (keep positioning)
PRIMARY precision: control 1.00, cgis-forced 1.00
  cgis-exp2-desc     n= 3 median ratio 0.59  Δrecall -0.15
  cgis-exp2-dir      n= 7 median ratio 0.80  Δrecall +0.00
  cgis-exp2-ind      n= 7 median ratio 0.65  Δrecall +0.00
  owner-exp1-big-dir n= 2 median ratio 0.91  Δrecall +0.00
  owner-exp1-big-ind n=11 median ratio 0.67  Δrecall +0.00
  owner-exp1-small-dir n= 4 median ratio 0.92  Δrecall +0.00
  owner-exp1-small-ind n= 3 median ratio 0.65  Δrecall +0.00
  ALL n=37 median ratio 0.74
```

**Verdict: the primary gate passes.** On the 14 big-radius indirect/dynamic tasks, forced cgis cuts
median cost to ×0.63 (CI 0.50–0.76, entirely below 0.80) at median Δrecall 0.00. The positioning
stays, as pre-committed. Over all 37 tasks the ratio is ×0.74; the saving is largest where the
chain is indirect (×0.59–0.67) and smallest on direct chains (×0.80–0.92), the shape a graph
predicts. Median turns fell from 8 (control) to 5; the median forced session made 1 cgis call and
read 0 files. Ingest (median 20 s) is not in `cost_usd` and would add wall time unforced.

**The median hides a systematic tail, and it is where the graph is blind.** 21 of 111 sessions
missed gold, and the misses are not random:

- The 3 `desc` tasks (dynamic dispatch) lose recall: −0.05, −0.15, −0.27. All three miss the same
  shape: the `@refreshes_graph` wrapper that runs the whole ingest from 14 MCP tools, and
  `BaseProvider._retry`. Exp2's control found these by reading code; the forced arm took the graph's
  answer and in most of those sessions read no file.
- `owner-exp1-big-ind-calculate-total-price` misses the `FavoritesService` chain in 3/3 runs
  (Δrecall −0.14), and `Worker.*` SAQ tasks recur across tasks. These look like resolver gaps of the
  #632 kind; not yet confirmed per edge.
- 99 of 111 sessions read no file at all. The MCP server instructions say "trust the answer for what it
  covers; do not re-grep to confirm a resolved edge". The agent obeyed, including on edges the graph
  does not cover. The instructions do also say to name the gap. The agents did not do that.

So the gate says cgis saves money at equal *median* recall. It does not say the answer is safe to
trust where dispatch is dynamic: there, the cheaper answer is a less complete one. That is the
remaining risk of the positioning. It argues for (a) closing the resolver gaps behind the misses and
(b) making the server's coverage note actionable (say *which* gap applies to this answer), before an
unforced adoption experiment.

**Limits.** The control is not concurrent (same day, same model and harness; stored). Forced use is
an upper bound, not adoption. n=14 primary tasks over two codebases, one of them cgis itself.
