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
stays, as pre-committed. An independent recomputation reproduced the numbers (geometric mean 0.631,
ratio of summed per-task medians 0.649, pooled over runs 0.640). Forced rows ran at cgis_sha 729834a, which differs from the pre-registered
c74c90d only in docs and the analysis script.

Over all 37 tasks the ratio is ×0.74, lower on indirect cells (×0.59–0.67) than direct ones
(×0.80–0.92). That is consistent with a graph helping more on indirect chains, but it is
**confounded with task size**: across tasks the ratio falls as control cost rises (Spearman −0.50),
because cheap tasks carry the forced arm's fixed overhead (a ToolSearch call in 107/111 sessions,
129 calls refused by the cgis-first guard, a longer prompt). Big-direct has
n=2, and per-task direct ratios (0.65–1.16) overlap the indirect ones. Median turns fell from 8 to 5;
the median forced session made 1 cgis call. Ingest is not in `cost_usd` (owner-api ~21 s, cgis ~3 s).

**The median hides a heavier tail, and it sits where the graph is wrong or blind.** The control also
missed gold in 21/111 sessions, but the forced misses are larger: total recall deficit 3.72 vs 1.67,
11 vs 3 sessions below 0.90, primary-set mean Δrecall −0.04 against the median 0.00 (5 of 14 tasks
negative; 8 tasks have Δ 0.00, 7 of them at 1.00 in both arms).

- Two of the three `desc` tasks lose recall (relative −0.15, specifier-fqn −0.27; record-usage
  *gains* +0.05). Their misses are the MCP tools reached only through the `@refreshes_graph`
  wrapper, which the graph does not link to the tools, and chains behind polymorphic
  `pack.classify` / `pack.scope_of` dispatch. record-usage misses only `BaseProvider._retry` (a thunk
  callback with no edge). The control mostly found these chains, though not always (relative run 0
  missed the same 11 tools).
- `calculate-total-price` misses the FavoritesService chain 3/3 because the graph resolves a call on
  an `Any`-typed attribute to `typing.Any.build_cards` **at confidence 1.0**. The same defect causes
  opensearch-mget run 1 (recall 0.50, `typing.Any.mget`). owner-api has 195 such edges. They are
  confident wrong edges, invisible to the coverage note, and a different defect from #632.
- `Worker.reset_showcase_data` is a real `cls._run_reset` resolver gap (forced missed it 4 times,
  control 2). `Worker.index_top_up` is a **gold spelling defect** (alias `TransactionWorker`, exp1
  I1), not a graph miss; it penalised the control too.
- 99 of 111 sessions never read a file with Read, but 61 of 111 inspected source with Grep or Bash after their
  first cgis call. Agents did look at source in most sessions, and several named the gap in their answer
  (unresolved `wrapper`, `pack.classify`, `cls._run_reset`), but did not follow it to recover the
  chain. None flagged the `typing.Any.*` edges, which look confident and so are invisible to them.

So the gate says cgis saves money at equal *median* recall. Where dispatch is dynamic, or where the
graph is confidently wrong, the cheaper answer is less complete. That is the remaining risk of the
positioning. Before an unforced adoption experiment it argues for: (a) fixing the `typing.Any.*`
edges, which are resolved at confidence 1.0, so no coverage note can surface them; (b) linking
decorator wrappers and `cls.` calls; (c) making the server's coverage note name the gap that applies
to this answer.

**Limits.** The control is not concurrent (same day, same model and harness; stored; transcripts not
kept, so no per-transcript comparison). Forced use is an upper bound, not adoption. The bootstrap is
over tasks and ignores run-to-run variance. Control precision for dir-extractors is the stored 0.36
(secondary table only). n=14 primary tasks over two codebases, one of them cgis itself.

<!-- START_CGIS_RELATED -->
**Related:** [exp2-indirection-cgis](2026-10-15-exp2-indirection-cgis.md) · [exp1-radius-indirection](2026-10-15-exp1-radius-indirection.md)

<!-- END_CGIS_RELATED -->
