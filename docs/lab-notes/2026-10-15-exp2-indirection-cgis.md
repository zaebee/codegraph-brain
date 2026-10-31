# Exp2: indirection at fixed radius and answer size, on cgis — pre-registration

Date: 2026-10-15. Committed before any exp2 session, and before the final cell assignments are known.
Follows exp1 (`2026-10-15-exp1-radius-indirection.md`, #633). There, radius, answer size and indirection
were confounded: FastAPI `Depends` makes every long chain indirect.

## Question

**H2:** at equal radius (files) and equal answer size (required symbols), does an indirect hop on the
caller chain raise an agent's cost, or lower its recall, on "who can end up calling X"?

## Corpus and units

- **Corpus:** cgis itself at a5e175e (`src/`, no DI framework), so long chains need not be indirect.
- **Candidates:** drawn by the cgis graph and re-derived independently, in the exp1 order:
  1. the cgis closure;
  2. an AST closure over final call names;
  3. an adjudicator reading the code with file:line evidence, and searching specifically for callers
     both tools miss: polymorphic upward dispatch, callbacks, registries, and inherited methods called
     through a subclass name.
- **Gold for dynamic dispatch is path-sensitive** (the exp1 C1 lesson): a caller counts only if some
  concrete path reaches the target.
- **Edge kinds:** `direct` (a function call, `self.m()` / `cls.m()` on the same class, or `Class.m()`);
  `attr_dep`; `param_obj`; `polymorphic`; `callback`; `registry`.
- **Indirect task:** some caller is reachable *only* through a non-direct edge.
- **Rulings fixed now:**
  - the `@refreshes_graph` decorator wrapper is plumbing: neither a caller nor a hop;
  - MCP dispatch by tool name and Typer registration are excluded;
  - gold accepts subclass spellings, both spellings of nested functions, and import-alias spellings.

## Pairing (algorithm fixed now, applied after adjudication)

Over the adjudicated pool, match each direct task with the unused indirect task that minimises
10·|Δfiles| + |Δsymbols|, subject to |Δfiles| ≤ 1 and |Δsymbols| ≤ 4. Direct tasks are taken in
ascending (files, symbols) order. Unpaired tasks are not run. If fewer than 6 pairs form, the result is
reported descriptively only.

## Runs

`control` arm, `claude-sonnet-5-5`, 3 runs per task, `--max-budget-usd 2.0`, on the organisation API
key. Every transcript must show `apiKeySource: ANTHROPIC_API_KEY`.

## Gates

Per pair, take the ratio of median cost (indirect / direct) and the difference of median recall
(direct − indirect).

1. **H2, cost:** the median pair ratio is ≥ 1.3 **and** the indirect task costs more in at least 75%
   of the pairs (rounded up). H2 on cost is refuted if the median ratio is ≤ 1.1. In between is
   inconclusive.
2. **H2, recall:** the median pair recall difference is ≥ 0.10.
3. **Descriptive:** results split by hop kind. "Dynamic" means `polymorphic`, `callback` or
   `registry`; "held object" means `attr_dep` or `param_obj`. No gate, because cells will be small.
4. **Ceiling:** stated both per run and per pair median (the exp1 G4 ambiguity, fixed here).

A bootstrap 95% interval over pairs is reported for gate 1.

## Rulings after adjudication, before pairing

- **Contested gold excluded.** `triads.tangle_mass` has 7 callers or 33, depending on whether a
  `@property` read, plus `getattr` on a registered component name, counts as a call.
  `paths.is_test_path` jumps past 50 callers if implicit protocol calls count: `with SQLiteStore(...)`
  → `__enter__`, or a pydantic validator on every `Node(...)`. A task whose gold rests on a contested
  rule tests the rule, not the agent, so both are dropped.
- **Constructor calls count as calls to `__init__`.**
- **Out of band, run as unpaired descriptive tasks:** `go.naming._relative` (35 symbols / 14 files),
  `js_globals.specifier_fqn` (36 / 14) and `BaseProvider._record_usage` (29 / 11). All three were
  drawn as direct; cgis classed them that way only because it does not follow polymorphic dispatch
  upward (a pack override, `BaseProvider.generate_structured`), nor `pack.classify` in the resolver.
  That is a resolver gap like #632.
- **Hop kinds in the pairable pool:** every indirect candidate in the size band is indirect through
  `param_obj` and/or `attr_dep` only. Polymorphic, callback and registry hops occur only in the large,
  out-of-band tasks. So gate 3 (dynamic against held-object) cannot be tested on pairs.
- **Seen before pairing:** I saw files and symbol counts per target, and nothing else.

## Pairs (algorithm applied as registered) and the gold-check pilot

8 pairs, at or above the minimum of 6. Δfiles ≤ 1 and Δsymbols ≤ 4 in every pair:

| # | direct (files/symbols) | indirect (files/symbols) |
|---|---|---|
| 1 | reject_metrics_path 3/5 | GuardianReviewer._finder_pass 3/5 |
| 2 | render_finding 3/5 | _segment_exclusion 3/9 |
| 3 | build_ladder 3/5 | refresh_dispatch 4/5 |
| 4 | resolve_fqn 3/12 | _as_terms 3/11 |
| 5 | languages.extractors 4/4 | SQLiteStore.get_edge_stats 4/5 |
| 6 | prompt._loc 4/9 | _sanitize_finder_result 4/10 |
| 7 | visible_findings 5/6 | DriftScorer.fit_templates 5/9 |
| 8 | effective_prefix 6/14 | TypeResolver._first_top_level_arg 7/12 |

Descriptive, unpaired: `_relative` (35/14), `specifier_fqn` (32/14 after the path-sensitive rule
removed 4 argument-infeasible callers) and `_record_usage` (29/11).

**Gold-check pilot (does not count):** pairs 1 and 8 plus `specifier_fqn`, 1 run each, to catch gold
defects before the full run, as exp1's pilot did. Any gold change it causes is recorded here before
the full run starts. The full run is all 19 tasks × 3 fresh runs.

## Gold-check pilot: findings and corrections (before the full run)

5 sessions, all on the organisation key; they do not count. Two gold defects:

1. **[Incomplete: the refresh path was added only where gold held `IngestionPipeline.run`; it missed `extractors`, see Post-review C1.]** **The decorator ruling was wrong in one direction.** `@refreshes_graph`'s wrapper calls the tool,
   and it also calls `refresh_if_stale` → `IngestionPipeline.run`, so all 14 decorated MCP tools really
   reach the whole ingest chain. Agents named those tools, correctly. Corrected rulings:
   - the wrapper and the refresh path are real callers in gold;
   - for classification, a decorator hop counts as **direct**, because it is named at the decorated
     definition, so no task changes class;
   - the wrapper itself is **optional**: agents do not name it, and requiring it would measure
     decorator awareness, not the indirection under test.
2. **`specifier_fqn` file list.** It kept `python/resolution.py` after the argument-infeasible symbols
   were removed. Files are now recomputed from the gold symbols.

The refresh path enlarged `refresh_dispatch` (5 → 20 symbols) and `_first_top_level_arg` (12 → 27),
which broke their pairs. **The pre-registered pairing algorithm was re-applied, unchanged, to the
corrected pool.** It gives 7 pairs, at or above the minimum of 6:

| # | direct (files/symbols) | indirect (files/symbols) |
|---|---|---|
| 1 | reject_metrics_path 3/5 | GuardianReviewer._finder_pass 3/5 |
| 2 | languages.extractors 4/4 | SQLiteStore.get_edge_stats 5/6 |
| 3 | build_ladder 4/6 | _sanitize_finder_result 4/10 |
| 4 | resolve_fqn 4/13 | _as_terms 4/12 |
| 5 | visible_findings 5/6 | SQLiteStore.get_incoming_edges 5/8 |
| 6 | prompt._loc 5/10 | DriftScorer.fit_templates 6/10 |
| 7 | effective_prefix 7/15 | drift._fit_config 6/12 |

The pairing saw counts that include the wrapper. In the task files the wrapper is optional, so the
**required** counts as run are:

| # | direct (files/symbols) | indirect (files/symbols) | Δsymbols |
|---|---|---|---|
| 1 | 3/5 | 3/5 | 0 |
| 2 | 4/4 | 4/5 | 1 |
| 3 | 3/5 | 4/10 | **5** |
| 4 | 3/12 | 3/11 | 1 |
| 5 | 5/6 | 4/7 | 1 |
| 6 | 4/9 | 5/9 | 0 |
| 7 | 6/14 | 5/11 | 3 |

Pair 3 now exceeds the ±4 tolerance by 1, because its direct side lost the wrapper and its indirect side
did not. It stays, and it is flagged: the analysis is reported with and without pair 3.
The descriptive tasks are `_relative` (47/14), `specifier_fqn` (47/13) and `_record_usage` (29/11).
The table above supersedes the earlier pairs table.

## Result (full run, 2026-10-15)

51 sessions, all billed to the organisation key (`apiKeySource: ANTHROPIC_API_KEY` ×51), 0 errors,
0 contaminated, $4.79. Model `claude-sonnet-5-5`, control arm, 3 runs per task.

| # | direct | cost | recall | indirect | cost | recall | ratio |
|---|---|---|---|---|---|---|---|
| 1 | reject_metrics_path | $0.073 | 1.00 | _finder_pass | $0.078 | 1.00 | 1.06 |
| 2 | extractors [gold corrected post-review; pair invalid, C1] | $0.074 | 1.00 | get_edge_stats | $0.057 | 1.00 | 0.77 |
| 3 | build_ladder | $0.065 | 1.00 | _sanitize_finder_result | $0.098 | 0.93 | 1.52 |
| 4 | resolve_fqn | $0.060 | 1.00 | _as_terms | $0.083 | 1.00 | 1.39 |
| 5 | visible_findings | $0.071 | 1.00 | get_incoming_edges | $0.077 | 1.00 | 1.08 |
| 6 | _loc | $0.078 | 1.00 | fit_templates | $0.081 | 1.00 | 1.04 |
| 7 | effective_prefix | $0.073 | 1.00 | _fit_config | $0.099 | 1.00 | 1.35 |

| gate | all 7 pairs | without pair 3 |
|---|---|---|
| G1 cost | **[Superseded, see Post-review C1: pair 2 invalid; 6 pairs → ×1.22 INCONCLUSIVE.]** median ratio **1.08** (bootstrap 95% CI 1.04–1.39); indirect costs more in 6 of 7 → **refuted** by the registered rule (≤ 1.1) | 1.07 (CI 0.90–1.37); 5 of 6 → refuted |
| G2 recall | median Δ +0.00 → not met | +0.00 → not met |
| G4 ceiling | per run 40/42 at 1.00 (min 0.93); per pair-task median 13/14 at 1.00 | — |

Descriptive tasks with dynamic hops (polymorphic, callback, registry), unpaired:

| task | required symbols / files | cost | recall |
|---|---|---|---|
| `_record_usage` | 29/11 | $0.128 | 0.93 |
| `_relative` | 46/14 | $0.203 | 0.97 |
| `specifier_fqn` | 46/13 | $0.192 | 0.97 |

### Reading

- **[Superseded: the verdict is INCONCLUSIVE; see Post-review corrections C1.]** **H2 as registered is refuted.** At matched radius and answer size, a held-object or parameter hop
  (`attr_dep` / `param_obj`) does not raise cost by ≥30%. The median is +8%.
- **[Softened post-review (I1): three pairs show +35–52%, so the data do not exclude an effect of 30% or more.]** **The direction is consistent.** The indirect task costs more in 6 of 7 pairs (a two-sided sign test
  gives about p = 0.13). So a small effect, in the 5–10% range, stays plausible. Its size is the point:
  it is small next to exp1's ×1.5–1.7.
- **[Overreach, softened post-review (I4): "consistent with" at most.]** **Together with exp1:** exp1's large indirection ratio came mostly from answer size and radius,
  which were confounded with indirection in owner-api. Matched, the held-object indirection that
  dominates both corpora costs little.
- **Recall stays at the ceiling** in both corpora once the gold is right. Read-only "who calls X" does
  not separate structures by correctness for this model.
- **Dynamic hops are not tested by the pairs.** The three dynamic tasks are 1.7–2.7× dearer than the paired median and
  recall 0.93–0.97, but their answers are 29–46 symbols, so size explains as much as the hops do.
- **Gold derivation was again the weakest link.** One adjudication rule (the decorator) was wrong, and
  the 5-session pilot caught it before the full run.

### Next

Read-only impact questions are saturated. To test whether structure matters for correctness, the
next experiment needs **edits with hidden tests** (exp1's H3–H5). Dynamic dispatch needs its own
pairs, matched on answer size, which requires a corpus with small polymorphic chains.

## Post-review corrections (PR #634 independent review; supersedes the verdict above)

Every number above reproduces. All 51 transcripts rescore with 0 mismatches against the YAML as run,
commit order is clean, and the registered algorithm reproduces the pairs, given an alphabetical
tie-break by FQN that was not registered. One gold defect changes the verdict, though.

**C1: `dir-extractors` missed the refresh path.** `_refresh` calls `extractors(source_roots)` directly
(auto_refresh.py:81). So the 14 `@refreshes_graph` MCP tools reach it through the wrapper, as they reach
`refresh_dispatch`. My post-pilot correction added the refresh path only where gold contained
`IngestionPipeline.run`, and `extractors` sits beside `run`, not under it. All 3 runs named those 14
tools and scored precision 0.36–0.38: once again the agents were right. With the YAML corrected
(18 required symbols), they score 1.00 / 1.00.

Pair 2 is then not a matched pair: 18 required symbols against `get_edge_stats`'s 5 required (6 counting
the optional wrapper, the basis the pairing used), beyond ±4 on either basis. It was also the
one pair where the indirect side was cheaper, and the reason was the 14 extra tools in the direct
answer. Dropping it leaves 6 pairs, the registered minimum
(`benchmarks/agent_ab/analysis/exp2_post_review.py`):

| | value |
|---|---|
| pair ratios | 1.06, 1.52, 1.39, 1.08, 1.04, 1.35 |
| median ratio | **1.22** (bootstrap 95% CI 1.05–1.45; geometric mean 1.22) |
| indirect dearer | 6 of 6 |
| G1 verdict | **INCONCLUSIVE**: between the registered ≤ 1.1 (refute) and ≥ 1.3 (pass) |

Re-running the registered algorithm on the corrected pool instead pairs `extractors` with
`refresh_dispatch`, which never ran, and reshuffles pairs 5–6, so no verdict can be computed from the
sessions that exist. That option was not taken.

**Sensitivity to pair 3 (delta review).** Pair 3 itself exceeds ±4 on required counts (Δ5; see the
pairs-as-run table). Dropping it as well leaves 5 pairs, below the registered minimum of 6, so the
result would be descriptive only. Those 5 pairs (1.06, 1.39, 1.08, 1.04, 1.35) have a median of
**1.08**, on the refute side. **The INCONCLUSIVE verdict therefore depends on keeping pair 3.** The
honest summary is "unstable", not "between thresholds".

**Reproduction note:** `analysis/exp2_gates.py` with the committed `exp2_pairs.json` reproduces the
superseded pre-review "REFUTED" by design, because it is the code of record. The corrected figures
come from `analysis/exp2_post_review.py`, which needs the local, git-ignored transcripts to rescore.
Without them it prints the stored scores.

**I1, the ratios are bimodal:** 1.04–1.08 against 1.35–1.52. A hierarchical bootstrap over pairs and
runs puts P(median ≥ 1.3) ≈ 0.36 and P(median ≤ 1.1) ≈ 0.34. The data neither exclude nor show a
≥ 30% effect.

**I2, pairing on wrapper-inclusive counts:** the pairing saw counts that include the optional wrapper.
Re-pairing on the required counts as run would give 8 pairs, 5 of them different. That choice was
made after the pilot. It was disclosed but not registered.

**I3, a weak manipulation in 3 pairs:** `_finder_pass`, `_sanitize_finder_result` and `_as_terms`
are "indirect" only through a local object (`reviewer = GuardianReviewer(...)`, or
`with DuckDBAnalyzer(...) as analyzer`). Grep finds those call sites as easily as direct calls. Two of
the three high-ratio pairs (3 and 4) are local-object pairs, which argues against reading the high
ratios as an indirection effect at all.

**I4:** the exp1 comparison overreached, as marked inline.

**Minor:**
- The adjudicated pool sizes existed (18:31–18:37) before the pre-registration commit (18:38). No
  outcome data existed yet.
- Task notes saying "wrapper removed" are stale.
- The pair-3 recall miss is a nested closure reached by a direct call, so it is not an indirection signal.
- The experiment cost $5.38 including the $0.58 pilot.
- The gate scripts are now committed verbatim in `benchmarks/agent_ab/analysis/`.

### Revised reading

- **H2 is unresolved on cgis.** With 6 valid pairs, the indirect side is dearer in all 6, by a median
  of 22%. That sits between the registered thresholds, and the split looks driven by a few pairs, two of
  them weak manipulations.
- **Recall is at the ceiling** in both experiments. On read-only impact questions this model does not
  separate structures by correctness.
- **Gold derivation had errors in every round** of both experiments: the exp1 pilot, the exp1
  review, the exp2 pilot and the exp2 review. A pilot or a reviewer caught each one. **Wherever the
  agents and the gold disagreed, the agents were right.** That has a selection effect built in:
  errors were found by reading those disagreements, so an error the agents shared would stay
  invisible (exp1 M5). The agents also made real errors of their own: classes that do not exist
  (`ReservationEffectsMixin`, `TopUpHandler`) and missed nested functions. So "agents produce better
  impact sets than our pipeline" is **not** established. What is established: aggregate rules applied
  mechanically (decorator, refresh path, `getattr` fan-out) failed at their boundaries, where
  per-path reading did not. Gold for agent benchmarks needs its own validation step: score it against
  several independent agent runs, and adjudicate every disagreement, before freezing it.

<!-- START_CGIS_RELATED -->
**Related:** [exp3-does-cgis-help](2026-10-15-exp3-does-cgis-help.md) · [exp1-radius-indirection](2026-10-15-exp1-radius-indirection.md)

<!-- END_CGIS_RELATED -->
