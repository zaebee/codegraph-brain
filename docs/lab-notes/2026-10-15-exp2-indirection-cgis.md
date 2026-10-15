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

1. **The decorator ruling was wrong in one direction.** `@refreshes_graph`'s wrapper calls the tool,
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
