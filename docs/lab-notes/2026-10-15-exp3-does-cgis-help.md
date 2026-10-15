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
